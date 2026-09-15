"""
Observação de DOM. Um único probe JS coleta todos os sinais de uma vez.

Por que um probe só, e não uma sequência de find_elements: cada ida e volta ao
driver custa dezenas de ms e vê o DOM num instante diferente. Um probe atômico
devolve um retrato coerente e roda igual em Selenium (execute_script),
Playwright (evaluate) e CDP (Runtime.evaluate) — que é o que permite os três
exemplos de integração sem duplicar lógica.

O probe é somente leitura no que importa: ele instala observadores (Mutation,
CSP, error, window.open) para *registrar* o que a página faz. Não clica, não
preenche, não injeta token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .exceptions import BridgeError, ProbeError

# --------------------------------------------------------------------------- #
# Bridges: a única parte que sabe qual biblioteca está dirigindo o browser.
# --------------------------------------------------------------------------- #


@runtime_checkable
class BrowserBridge(Protocol):
    """Mínimo que o orquestrador precisa de um browser."""

    def eval_js(self, script: str, arg: Any = None) -> Any:
        """Executa `script` como corpo de função que recebe `arg` e retorna JSON-serializável."""

    def url(self) -> str: ...

    def cookies(self) -> list[dict[str, Any]]: ...

    def screenshot(self, path: str) -> bool: ...

    def janelas(self) -> list[str]:
        """Identificadores de aba/janela. Usado para detectar popup."""


def envolver_selenium(script: str) -> str:
    """
    Corpo de função -> script que `driver.execute_script(js, arg)` sabe rodar.

    Os scripts deste módulo (`_PROBE_JS`, `_RECORDER_JS`) são CORPO de função e
    esperam um parâmetro chamado `arg`. Rodá-los crus dá
    `javascript error: arg is not defined`.

    Isto é função nomeada, e não f-string solta dentro da bridge, porque o
    envelope já foi motivo de bug: quando a API HTTP servia o probe para a RPA
    rodar por conta própria, ela entregava o CORPO CRU e todo probe do lado da
    RPA falhava — 8 alvos reais, 8 falhas, com 21 testes verdes. Esse endpoint
    não existe mais (a API ficou só com leitura de imagem e áudio), mas a lição
    fica: quem for servir ou copiar estes scripts passa por aqui.
    """
    return f"return (function(arg){{{script}}})(arguments[0]);"


class SeleniumBridge:
    """Bridge para Selenium WebDriver (Chrome/Edge, attachado ou lançado)."""

    def __init__(self, driver):
        self.driver = driver

    def eval_js(self, script: str, arg: Any = None) -> Any:
        try:
            return self.driver.execute_script(envolver_selenium(script), arg)
        except Exception as exc:  # WebDriverException e derivados
            raise BridgeError(f"execute_script falhou: {exc}") from exc

    def url(self) -> str:
        try:
            return self.driver.current_url or ""
        except Exception as exc:
            raise BridgeError(str(exc)) from exc

    def cookies(self) -> list[dict[str, Any]]:
        try:
            return list(self.driver.get_cookies())
        except Exception:
            return []

    def screenshot(self, path: str) -> bool:
        try:
            return bool(self.driver.save_screenshot(path))
        except Exception:
            return False

    def janelas(self) -> list[str]:
        try:
            return list(self.driver.window_handles)
        except Exception:
            return []

    def instalar_no_documento(self, script: str) -> bool:
        """Roda o script antes de qualquer JS da página, em toda navegação."""
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": f"(function(){{ var arg = null; {script} }})();"},
            )
            return True
        except Exception:
            return False


class PlaywrightBridge:
    """Bridge para Playwright sync API (`page`)."""

    def __init__(self, page):
        self.page = page

    def eval_js(self, script: str, arg: Any = None) -> Any:
        try:
            return self.page.evaluate(f"(arg) => {{{script}}}", arg)
        except Exception as exc:
            raise BridgeError(f"page.evaluate falhou: {exc}") from exc

    def url(self) -> str:
        return self.page.url or ""

    def cookies(self) -> list[dict[str, Any]]:
        try:
            return list(self.page.context.cookies())
        except Exception:
            return []

    def screenshot(self, path: str) -> bool:
        try:
            self.page.screenshot(path=path)
            return True
        except Exception:
            return False

    def janelas(self) -> list[str]:
        try:
            return [p.url for p in self.page.context.pages]
        except Exception:
            return []

    def instalar_no_documento(self, script: str) -> bool:
        try:
            self.page.add_init_script(f"(function(){{ var arg = null; {script} }})();")
            return True
        except Exception:
            return False


class CDPBridge:
    """
    Bridge falando CDP puro, sem Selenium nem Playwright. Espera um objeto com
    `send(metodo, params) -> dict` (ex.: websocket para /devtools/page/<id>).
    """

    def __init__(self, cdp, session_id: str | None = None):
        self.cdp = cdp
        self.session_id = session_id

    def eval_js(self, script: str, arg: Any = None) -> Any:
        expressao = f"(function(arg){{{script}}})({json.dumps(arg)})"
        res = self.cdp.send(
            "Runtime.evaluate",
            {"expression": expressao, "returnByValue": True, "awaitPromise": True},
        )
        resultado = (res or {}).get("result", {})
        if resultado.get("subtype") == "error" or "exceptionDetails" in (res or {}):
            raise BridgeError(f"Runtime.evaluate: {resultado.get('description') or res}")
        return resultado.get("value")

    def url(self) -> str:
        return self.eval_js("return location.href;") or ""

    def cookies(self) -> list[dict[str, Any]]:
        try:
            return list((self.cdp.send("Network.getCookies", {}) or {}).get("cookies", []))
        except Exception:
            return []

    def screenshot(self, path: str) -> bool:
        import base64

        try:
            data = (self.cdp.send("Page.captureScreenshot", {}) or {}).get("data")
            if not data:
                return False
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(data))
            return True
        except Exception:
            return False

    def janelas(self) -> list[str]:
        try:
            alvos = (self.cdp.send("Target.getTargets", {}) or {}).get("targetInfos", [])
            return [t.get("targetId", "") for t in alvos if t.get("type") == "page"]
        except Exception:
            return []

    def instalar_no_documento(self, script: str) -> bool:
        try:
            self.cdp.send("Page.enable", {})
            self.cdp.send("Page.addScriptToEvaluateOnNewDocument",
                          {"source": f"(function(){{ var arg = null; {script} }})();"})
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# O que o probe procura. Preenchido a partir dos SIGNATURE dos adapters.
# --------------------------------------------------------------------------- #


@dataclass
class ProbeSpec:
    response_fields: list[str] = field(default_factory=list)
    container_selectors: list[str] = field(default_factory=list)
    globals_: list[str] = field(default_factory=list)
    script_hosts: list[str] = field(default_factory=list)
    custom_elements: list[str] = field(default_factory=list)
    frame_hosts: list[str] = field(default_factory=list)
    #: Chaves de storage/cookie que indicam token de fornecedor.
    token_keys: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "responseFields": self.response_fields,
            "containerSelectors": self.container_selectors,
            "globals": self.globals_,
            "scriptHosts": self.script_hosts,
            "customElements": self.custom_elements,
            "frameHosts": self.frame_hosts,
            "tokenKeys": self.token_keys,
        }


# --------------------------------------------------------------------------- #
# Recorder: instalado uma vez por documento, acumula eventos entre polls.
# --------------------------------------------------------------------------- #

_RECORDER_JS = r"""
if (window.__co_rec && window.__co_rec.v === 3) { return {ja: true}; }

const rec = {
  v: 3,
  mutacoes: 0,
  framesCriados: 0,
  framesRemovidos: 0,
  csp: [],
  erros: [],
  popups: [],
  callbacksDisparados: [],
  instaladoEm: Date.now(),
};
window.__co_rec = rec;

// CSP: a única forma confiável de saber que o iframe/script do fornecedor foi
// barrado por politica da página, e não por rede.
addEventListener('securitypolicyviolation', (e) => {
  if (rec.csp.length < 40) {
    rec.csp.push({
      diretiva: e.violatedDirective || e.effectiveDirective || '',
      bloqueado: String(e.blockedURI || '').slice(0, 200),
      origem: String(e.sourceFile || '').slice(0, 200),
    });
  }
}, true);

addEventListener('error', (e) => {
  if (rec.erros.length >= 40) return;
  const alvo = e.target;
  if (alvo && (alvo.tagName === 'SCRIPT' || alvo.tagName === 'IFRAME' || alvo.tagName === 'IMG')) {
    // Falha de subrecurso: distingue script que nao carregou de script com bug.
    rec.erros.push({tipo: 'subrecurso', tag: alvo.tagName, url: String(alvo.src || '').slice(0, 200)});
  } else {
    rec.erros.push({tipo: 'js', msg: String(e.message || '').slice(0, 300),
                    arquivo: String(e.filename || '').slice(0, 200)});
  }
}, true);

addEventListener('unhandledrejection', (e) => {
  if (rec.erros.length < 40) {
    rec.erros.push({tipo: 'promise', msg: String((e.reason && e.reason.message) || e.reason || '').slice(0, 300)});
  }
}, true);

// Popup: GeeTest/Arkose podem abrir o desafio em outra janela. Só observamos.
const openOriginal = window.open;
window.open = function (...args) {
  try { rec.popups.push(String(args[0] || '').slice(0, 200)); } catch (_) {}
  return openOriginal.apply(this, args);
};

const mo = new MutationObserver((lista) => {
  rec.mutacoes += lista.length;
  for (const m of lista) {
    for (const n of m.addedNodes || []) {
      if (n.tagName === 'IFRAME') rec.framesCriados++;
    }
    for (const n of m.removedNodes || []) {
      if (n.tagName === 'IFRAME') rec.framesRemovidos++;
    }
  }
});
rec.mo = mo;
rec.observando = false;

// Quando instalado via addScriptToEvaluateOnNewDocument, isto roda ANTES de
// existir documentElement: observe() lançaria e o recorder ficaria mudo sem
// avisar. Por isso tenta agora e, se não der, reagenda.
function observar() {
  if (rec.observando) return true;
  const raiz = document.documentElement || document.body;
  if (!raiz) return false;
  try {
    mo.observe(raiz, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['src', 'style', 'class', 'value', 'data-sitekey'],
    });
    rec.observando = true;
  } catch (_) { return false; }
  return true;
}
if (!observar()) {
  document.addEventListener('readystatechange', observar);
  addEventListener('DOMContentLoaded', observar, {once: true});
}
return {ja: false};
"""

# --------------------------------------------------------------------------- #
# Probe principal: retrato completo do documento.
# --------------------------------------------------------------------------- #

_PROBE_JS = r"""
const spec = arg || {};
const out = {
  url: location.href,
  origemSegura: window.isSecureContext === true,
  protocolo: location.protocol,
  containers: [],
  responseFields: [],
  iframes: [],
  globals: {},
  scripts: [],
  customElements: [],
  shadowSuspeitos: [],
  storage: [],
  cookiesNomes: [],
  callbacksRegistrados: [],
  eventos: null,
  formularios: [],
};

// --- travessia atravessando shadow roots abertos -------------------------- //
function todosOsNos(raiz, acc, profundidade) {
  if (!raiz || profundidade > 12) return acc;
  let nos;
  try { nos = raiz.querySelectorAll('*'); } catch (_) { return acc; }
  for (const el of nos) {
    acc.push(el);
    if (el.shadowRoot) {                       // shadow root ABERTO
      acc.__shadowHosts = acc.__shadowHosts || [];
      acc.__shadowHosts.push(el);
      todosOsNos(el.shadowRoot, acc, profundidade + 1);
    }
  }
  return acc;
}
const nos = todosOsNos(document, [], 0);
const shadowHosts = nos.__shadowHosts || [];

function visivel(el) {
  try {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return !!(r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' &&
              cs.display !== 'none' && cs.opacity !== '0');
  } catch (_) { return false; }
}
function caixa(el) {
  try { const r = el.getBoundingClientRect();
        return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; }
  catch (_) { return null; }
}
function seletor(el) {
  if (!el) return null;
  if (el.id) return '#' + el.id;
  const cls = (typeof el.className === 'string' && el.className.trim())
    ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
  return (el.tagName || '').toLowerCase() + cls;
}

// --- containers por seletor conhecido + qualquer coisa com data-sitekey --- //
const vistos = new Set();
function registrarContainer(el, motivo) {
  if (!el || vistos.has(el)) return;
  vistos.add(el);
  const attrs = {};
  for (const a of el.attributes || []) {
    if (a.name.startsWith('data-') || a.name === 'class' || a.name === 'id') {
      attrs[a.name] = String(a.value).slice(0, 160);
    }
  }
  out.containers.push({
    motivo: motivo,
    seletor: seletor(el),
    tag: (el.tagName || '').toLowerCase(),
    attrs: attrs,
    visivel: visivel(el),
    caixa: caixa(el),
    // Container renderizado mas SEM filhos costuma ser widget que ainda não
    // subiu, ou shadow root fechado. Nos dois casos: pendente, não ausente.
    filhos: el.children ? el.children.length : 0,
    htmlLen: (el.innerHTML || '').length,
    temShadowAberto: !!el.shadowRoot,
  });
}
for (const sel of (spec.containerSelectors || [])) {
  let achados = [];
  try { achados = document.querySelectorAll(sel); } catch (_) {}
  for (const el of achados) registrarContainer(el, 'seletor:' + sel);
  for (const host of shadowHosts) {
    let dentro = [];
    try { dentro = host.shadowRoot.querySelectorAll(sel); } catch (_) {}
    for (const el of dentro) registrarContainer(el, 'shadow:' + sel);
  }
}
for (const el of nos) {
  if (el.hasAttribute && (el.hasAttribute('data-sitekey') || el.hasAttribute('data-pkey') ||
                          el.hasAttribute('data-captcha-id') || el.hasAttribute('challengeurl'))) {
    registrarContainer(el, 'atributo');
  }
}

// --- campos de resposta: por nome conhecido, id e sufixo --------------------//
const nomes = new Set(spec.responseFields || []);
for (const el of nos) {
  const tag = (el.tagName || '').toLowerCase();
  if (tag !== 'input' && tag !== 'textarea') continue;
  const nome = el.getAttribute('name') || '';
  const id = el.getAttribute('id') || '';
  const casa = nomes.has(nome) || nomes.has(id) ||
               /captcha|challenge|-response$|_response$|frc-|altcha|fc-token|aws-waf/i.test(nome + ' ' + id);
  if (!casa) continue;
  const valor = el.value == null ? '' : String(el.value);
  out.responseFields.push({
    nome: nome, id: id, tag: tag,
    vazio: valor.length === 0,
    len: valor.length,
    // Nunca devolvemos o token. Só tamanho e um prefixo curto para
    // reconhecer formato (ex.: '03A' do reCAPTCHA, 'P1_' do hCaptcha).
    prefixo: valor.slice(0, 3),
    oculto: (el.type || '') === 'hidden' || !visivel(el),
    dono: seletor(el.parentElement),
  });
}

// --- iframes: TODOS, inclusive 0x0 e display:none ------------------------- //
const hosts = spec.frameHosts || [];
let framesTodos = [];
try { framesTodos = Array.from(document.querySelectorAll('iframe')); } catch (_) {}
for (const host of shadowHosts) {
  try { framesTodos = framesTodos.concat(Array.from(host.shadowRoot.querySelectorAll('iframe'))); } catch (_) {}
}
for (const f of framesTodos) {
  const src = String(f.getAttribute('src') || f.src || '');
  const c = caixa(f) || {w: 0, h: 0};
  out.iframes.push({
    src: src.slice(0, 200),
    deFornecedor: hosts.some(h => src.includes(h)),
    visivel: visivel(f),
    zero: (c.w === 0 || c.h === 0),
    caixa: c,
    title: String(f.getAttribute('title') || '').slice(0, 80),
  });
}
out.totalIframes = framesTodos.length;

// --- globais expostas ----------------------------------------------------- //
for (const nome of (spec.globals || [])) {
  try {
    const partes = nome.split('.');
    let cur = window, ok = true;
    for (const p of partes) {
      if (cur == null || !(p in cur)) { ok = false; break; }
      cur = cur[p];
    }
    if (ok && cur !== undefined) out.globals[nome] = typeof cur;
  } catch (_) {}
}

// --- scripts do fornecedor: carregado? falhou? ---------------------------- //
for (const s of Array.from(document.scripts || [])) {
  const src = String(s.src || '');
  if (!src) continue;
  if ((spec.scriptHosts || []).some(h => src.includes(h))) {
    out.scripts.push({src: src.slice(0, 220), async: !!s.async, defer: !!s.defer});
  }
}

// --- custom elements (ALTCHA e afins) ------------------------------------- //
for (const nome of (spec.customElements || [])) {
  let els = [];
  try { els = Array.from(document.querySelectorAll(nome)); } catch (_) {}
  for (const el of els) {
    const attrs = {};
    for (const a of el.attributes || []) attrs[a.name] = String(a.value).slice(0, 160);
    out.customElements.push({
      nome: nome, attrs: attrs, visivel: visivel(el),
      definido: !!(window.customElements && window.customElements.get(nome)),
      temShadowAberto: !!el.shadowRoot,
      // ALTCHA expõe estado no próprio elemento.
      estado: (el.getAttribute && el.getAttribute('data-state')) || null,
    });
  }
}

// --- suspeita de shadow root FECHADO ------------------------------------- //
// Elemento com área renderizada, sem filhos no DOM claro e sem shadowRoot
// acessível: alguém desenhou algo que não conseguimos enxergar. É exatamente o
// caso que não pode ser lido como "não tem desafio".
for (const el of nos) {
  try {
    if (el.shadowRoot) continue;
    if (el.children && el.children.length > 0) continue;
    const t = (el.tagName || '').toLowerCase();
    if (['input','textarea','img','br','hr','script','style','link','meta','path','svg','use','source'].includes(t)) continue;
    const c = caixa(el);
    if (c && c.w >= 40 && c.h >= 20 && !(el.textContent || '').trim()) {
      out.shadowSuspeitos.push({seletor: seletor(el), tag: t, caixa: c});
    }
  } catch (_) {}
}
out.shadowSuspeitos = out.shadowSuspeitos.slice(0, 15);

// --- storage e cookies: só NOMES de chave, nunca valores ----------------- //
// ATENÇÃO: o simples ACESSO a window.localStorage lança em página que nega
// storage (política de cookies de terceiro, documento sandbox). Ler isso dentro
// de um literal de array deixava a exceção fora do try e derrubava o probe
// inteiro — quebrou de verdade num portal real. Por isso o acesso é lazy, cada um no
// seu try.
const chaves = spec.tokenKeys || [];
for (const rotulo of ['local', 'session']) {
  try {
    const st = rotulo === 'local' ? window.localStorage : window.sessionStorage;
    if (!st) continue;
    for (let i = 0; i < st.length; i++) {
      const k = st.key(i);
      if (!k) continue;
      if (chaves.some(c => k.includes(c)) || /captcha|token|challenge|arkose|geetest|altcha|frc/i.test(k)) {
        const v = st.getItem(k) || '';
        out.storage.push({onde: rotulo, chave: k.slice(0, 80), len: v.length});
      }
    }
  } catch (e) {
    out.storageBloqueado = (out.storageBloqueado || []).concat(rotulo);
  }
}
try {
  out.cookiesNomes = document.cookie.split(';')
    .map(c => c.split('=')[0].trim()).filter(Boolean).slice(0, 60);
} catch (_) {}

// --- callbacks declarados em data-callback existem no escopo global? ----- //
for (const c of out.containers) {
  const cb = c.attrs['data-callback'];
  if (cb) {
    out.callbacksRegistrados.push({nome: cb, existe: typeof window[cb] === 'function'});
  }
}

// --- estado do formulário dono do campo de resposta ---------------------- //
try {
  for (const form of Array.from(document.forms).slice(0, 8)) {
    out.formularios.push({
      seletor: seletor(form),
      action: String(form.getAttribute('action') || '').slice(0, 160),
      campos: form.elements ? form.elements.length : 0,
      // Quantos campos já têm conteúdo: é isso que precisa sobreviver à pausa.
      preenchidos: Array.from(form.elements || []).filter(
        e => e.value && String(e.value).length > 0 && e.type !== 'hidden').length,
      submitHabilitado: !Array.from(form.querySelectorAll('[type=submit]'))
        .some(b => b.disabled),
    });
  }
} catch (_) {}

// --- chamada do captcha desativada pelo PRÓPRIO portal ------------------- //
// Medido em produção:
//   function executarReCaptcha() { if (false) { grecaptcha.execute(); ... } ... }
// O portal carrega o api.js, expõe a global, e mantém a chamada morta atrás de
// um `if (false)`. Sem este sinal o solver espera a janela inteira de render
// (20 s medidos) para concluir que não há widget — por item, numa página que
// nunca vai ter captcha. A mesma heurística já é usada em produção pelo RPA
// (padrão visto em automações reais que checam se o captcha está ligado).
//
// Só lê script INLINE (sem src) e devolve um trecho curto, nunca o script todo.
out.captchaDesativado = '';
try {
  const morto = /if\s*\(\s*(?:false|0)\s*\)[\s\S]{0,120}?(grecaptcha|hcaptcha)\s*\.\s*(execute|render)/i;
  for (const s of Array.from(document.scripts || [])) {
    if (s.src) continue;
    const txt = String(s.textContent || '');
    if (txt.length > 200000) continue;          // não varrer bundle gigante
    const m = txt.match(morto);
    if (m) { out.captchaDesativado = m[0].replace(/\s+/g, ' ').slice(0, 160); break; }
  }
} catch (_) {}

// --- destino de cada script de fornecedor, via Resource Timing ----------- //
// Reconstrói o passado quando o recorder foi instalado depois do load: a
// violação de CSP e o erro de subrecurso já dispararam e não voltam. Aqui
// separamos três destinos que na tela são idênticos (widget não aparece):
//   carregou           -> resposta chegou (inclusive vinda do cache)
//   abortado           -> requisição falhou: nada chegou
//   nunca_buscado      -> tag no DOM e NENHUMA entrada: CSP ou bloqueio externo
//
// Usar 'duration 0 e transferSize 0' como falha é ERRADO: é exatamente a
// assinatura de um CACHE HIT. Deu falso positivo em produção (um reCAPTCHA real
// servido do cache foi lido como abortado). O discriminador é responseStatus:
// cache hit traz 200, requisição falha traz 0.
out.recursos = [];
try {
  const timing = performance.getEntriesByType('resource');
  for (const s of Array.from(document.scripts || [])) {
    const src = String(s.src || '');
    if (!src || !(spec.scriptHosts || []).some(h => src.includes(h))) continue;
    const e = timing.find(t => t.name === src);
    let destino, detalhe = null;
    if (!e) {
      destino = 'nunca_buscado';
    } else {
      const status = ('responseStatus' in e) ? e.responseStatus : null;
      const bytes = (e.transferSize || 0) + (e.decodedBodySize || 0) + (e.encodedBodySize || 0);
      if (status === 0 && bytes === 0) destino = 'abortado';
      else if (status === null && bytes === 0 && e.responseEnd === 0) destino = 'abortado';
      else destino = 'carregou';
      detalhe = {status: status, bytes: bytes, dur: Math.round(e.duration)};
    }
    out.recursos.push({url: src.slice(0, 200), destino: destino, detalhe: detalhe});
  }
} catch (_) {}

// --- eventos acumulados pelo recorder ------------------------------------ //
const rec = window.__co_rec;
if (rec) {
  out.eventos = {
    observando: rec.observando === true,
    mutacoes: rec.mutacoes,
    framesCriados: rec.framesCriados,
    framesRemovidos: rec.framesRemovidos,
    csp: rec.csp.slice(),
    erros: rec.erros.slice(),
    popups: rec.popups.slice(),
    idadeMs: Date.now() - rec.instaladoEm,
  };
  rec.mutacoes = 0; rec.csp.length = 0; rec.erros.length = 0; rec.popups.length = 0;
}
return out;
"""


class BrowserObserver:
    """Instala o recorder e tira retratos do documento."""

    def __init__(self, bridge: BrowserBridge):
        self.bridge = bridge
        self._janelas_iniciais: list[str] | None = None
        self._antecipado = False

    def preparar(self) -> bool:
        """
        Instala o recorder ANTES de qualquer script da página, para toda
        navegação futura. Chamar uma vez, no começo do RPA.

        Sem isso o recorder só entra depois do load, e a violação de CSP e o
        erro de subrecurso do próprio load já dispararam — não voltam. O probe
        tem reconstrução retroativa via Resource Timing como rede de segurança,
        mas ela não distingue CSP de bloqueio por extensão; a instalação
        antecipada distingue.
        """
        instalar = getattr(self.bridge, "instalar_no_documento", None)
        if instalar is None:
            return False
        self._antecipado = bool(instalar(_RECORDER_JS))
        return self._antecipado

    def garantir_recorder(self) -> bool:
        """
        Idempotente. Reinstala sozinho depois de navegação (o objeto se perde
        com o documento), que é o caso do 'reload parcial' do enunciado.
        """
        res = self.bridge.eval_js(_RECORDER_JS)
        return bool(res and res.get("ja") is False)

    def retrato(self, spec: ProbeSpec) -> dict[str, Any]:
        self.garantir_recorder()
        dados = self.bridge.eval_js(_PROBE_JS, spec.to_json())
        if not isinstance(dados, dict) or "containers" not in dados:
            raise ProbeError(f"probe retornou estrutura inesperada: {type(dados).__name__}")

        janelas = self.bridge.janelas()
        if self._janelas_iniciais is None:
            self._janelas_iniciais = list(janelas)
        dados["janelas"] = janelas
        dados["janelas_novas"] = max(0, len(janelas) - len(self._janelas_iniciais))
        dados["recorder_antecipado"] = self._antecipado
        return dados
