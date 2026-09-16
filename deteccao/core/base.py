"""
Interface dos adapters e a regra canônica de pendência.

A regra vive AQUI, uma vez só. Se cada adapter reimplementasse "está pendente?",
o falso negativo que motivou este framework voltaria em nove lugares diferentes.
Adapter só declara sua assinatura e refina variante/modalidade.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import CONFIG, Config
from .models import (
    CaptchaResult,
    DetectionResult,
    Evidence,
    Modality,
    Signal,
    State,
    Variant,
    Vendor,
    campo_preenchido,
    prefixo_do_campo,
    tamanho_do_campo,
)


@dataclass(frozen=True)
class Signature:
    """Como reconhecer um fornecedor. Tudo string literal vinda da doc oficial."""

    vendor: Vendor
    script_hosts: tuple[str, ...] = ()
    globals_: tuple[str, ...] = ()
    container_selectors: tuple[str, ...] = ()
    response_fields: tuple[str, ...] = ()
    frame_hosts: tuple[str, ...] = ()
    custom_elements: tuple[str, ...] = ()
    token_keys: tuple[str, ...] = ()
    #: Chaves oficiais de teste do fornecedor. Só para fixtures/CI.
    test_keys: tuple[str, ...] = ()
    #: Prefixo típico do token, para reconhecer formato sem logar o valor.
    token_prefixos: tuple[str, ...] = ()
    #: Campos que este fornecedor lê mas NÃO provam que é ele. O caso real:
    #: hCaptcha em modo compatibilidade também usa `g-recaptcha-response`, e
    #: aceitar isso como prova fazia o hCaptcha reivindicar página de reCAPTCHA.
    campos_compartilhados: tuple[str, ...] = ()


@dataclass
class Ctx:
    """
    Contexto de uma observação. Os adapters são funções puras sobre `retrato`:
    dá para testar todos eles com JSON de fixture, sem browser.
    """

    retrato: dict[str, Any]
    bridge: Any = None
    observer: Any = None
    telemetry: Any = None
    config: Config = field(default_factory=lambda: CONFIG)

    @property
    def url(self) -> str:
        return self.retrato.get("url") or ""

    def containers(self, adapter: "BaseAdapter") -> list[dict[str, Any]]:
        return [c for c in self.retrato.get("containers", []) if adapter.reconhece_container(c)]

    def campos(self, adapter: "BaseAdapter") -> list[dict[str, Any]]:
        nomes = set(adapter.SIGNATURE.response_fields)
        return [f for f in self.retrato.get("responseFields", [])
                if f.get("nome") in nomes or f.get("id") in nomes]

    def frames(self, adapter: "BaseAdapter") -> list[dict[str, Any]]:
        hosts = adapter.SIGNATURE.frame_hosts
        return [i for i in self.retrato.get("iframes", [])
                if any(h in (i.get("src") or "") for h in hosts)]

    def scripts(self, adapter: "BaseAdapter") -> list[dict[str, Any]]:
        hosts = adapter.SIGNATURE.script_hosts
        return [s for s in self.retrato.get("scripts", [])
                if any(h in (s.get("src") or "") for h in hosts)]

    def globais(self, adapter: "BaseAdapter") -> dict[str, str]:
        return {k: v for k, v in (self.retrato.get("globals") or {}).items()
                if k in adapter.SIGNATURE.globals_}

    @property
    def eventos(self) -> dict[str, Any]:
        return self.retrato.get("eventos") or {}


class CaptchaAdapter(Protocol):
    """Interface mínima exigida pelo orquestrador."""

    def detect(self, ctx: Ctx) -> DetectionResult: ...

    def inspect_state(self, ctx: Ctx) -> DetectionResult: ...

    def wait_until_ready(self, ctx_factory, timeout: float) -> DetectionResult: ...

    def wait_for_completion(self, ctx_factory, timeout: float) -> CaptchaResult: ...

    def validate_completion(self, ctx: Ctx) -> CaptchaResult: ...


class BaseAdapter:
    """
    Implementação comum. Subclasse declara SIGNATURE e, se precisar, refina
    `variante()` e `modalidade()`.
    """

    SIGNATURE: Signature
    #: Modalidade padrão do fornecedor. Sobrescrever quando variar por caso.
    MODALIDADE_PADRAO: Modality = Modality.VISUAL
    #: True se o próprio mecanismo do fornecedor emite o token sem interação
    #: (proof-of-work, score, challenge silencioso).
    AUTORRESOLVE: bool = False
    #: True para fornecedor que NUNCA renderiza widget (reCAPTCHA v3): nele a
    #: ausência de container/iframe/campo é o estado normal, não sinal de que o
    #: script carregou sem instanciar nada.
    SEM_WIDGET_POR_DESIGN: bool = False
    #: JS que ACIONA o mecanismo do fornecedor quando o site não o dispara
    #: sozinho — nada aqui computa token: quem computa é o widget. Vazio =
    #: não há nada para acionar (o widget já começa por conta própria).
    ACIONAR_JS: str = ""

    # ------------------------------------------------------------------ #
    # Reconhecimento
    # ------------------------------------------------------------------ #

    def reconhece_container(self, c: dict[str, Any]) -> bool:
        """
        Um container é meu se o probe casou pelo MEU seletor, ou se a classe bate
        token a token.

        Comparação por token, não por substring: '#captcha' do GeeTest casava
        dentro de 'g-recaptcha' e roubava página de reCAPTCHA.
        """
        motivo = c.get("motivo") or ""
        classes = set((c.get("attrs", {}).get("class") or "").split())
        ident = (c.get("attrs", {}).get("id") or "")
        for sel in self.SIGNATURE.container_selectors:
            if motivo in (f"seletor:{sel}", f"shadow:{sel}"):
                return True
            if sel.startswith(".") and sel[1:] in classes:
                return True
            if sel.startswith("#") and sel[1:] == ident:
                return True
        return False

    def campos_proprios(self, ctx: Ctx) -> list[dict[str, Any]]:
        """Campos que provam o fornecedor (exclui os compartilhados)."""
        compartilhados = set(self.SIGNATURE.campos_compartilhados)
        return [f for f in ctx.campos(self)
                if f.get("nome") not in compartilhados and f.get("id") not in compartilhados]

    def presente(self, ctx: Ctx) -> bool:
        """
        Exige pelo menos um sinal DISTINTIVO. Campo compartilhado com outro
        fornecedor não basta — senão um adapter reivindica a página do outro.

        Ausência de iframe continua não contando para nada.
        """
        return bool(
            ctx.containers(self)
            or self.campos_proprios(ctx)
            or ctx.scripts(self)
            or ctx.globais(self)
            or ctx.frames(self)
            or self._custom_elements(ctx)
        )

    def _custom_elements(self, ctx: Ctx) -> list[dict[str, Any]]:
        nomes = set(self.SIGNATURE.custom_elements)
        return [e for e in ctx.retrato.get("customElements", []) if e.get("nome") in nomes]

    # ------------------------------------------------------------------ #
    # Refinamentos por fornecedor
    # ------------------------------------------------------------------ #

    def variante(self, ctx: Ctx) -> Variant:
        containers = ctx.containers(self)
        for c in containers:
            tamanho = (c.get("attrs", {}).get("data-size") or "").lower()
            if tamanho == "invisible":
                return Variant.INVISIBLE
        if not containers and ctx.globais(self):
            return Variant.EXPLICIT_RENDER
        if any(f.get("visivel") for f in ctx.frames(self)):
            return Variant.CHECKBOX
        return Variant.AUTO_RENDER if containers else Variant.UNKNOWN

    def modalidade(self, ctx: Ctx) -> Modality:
        return self.MODALIDADE_PADRAO

    def sitekey(self, ctx: Ctx) -> str | None:
        for c in ctx.containers(self):
            attrs = c.get("attrs", {})
            for chave in ("data-sitekey", "data-pkey", "data-captcha-id", "data-sitekey-v3"):
                if attrs.get(chave):
                    return attrs[chave]
        for s in ctx.scripts(self):
            src = s.get("src") or ""
            if "render=" in src:
                return src.split("render=", 1)[1].split("&", 1)[0]
        return None

    # ------------------------------------------------------------------ #
    # A REGRA CANÔNICA — o coração do framework
    # ------------------------------------------------------------------ #

    def detect(self, ctx: Ctx) -> DetectionResult:
        """
        Correlaciona sinais e decide o estado.

        Invariante: `ausência de iframe` JAMAIS conclui ausência de desafio.
        Só se conclui resolvido com evidência positiva (campo preenchido, ou
        callback disparado, ou token em cookie/storage).
        """
        if not self.presente(ctx):
            return DetectionResult(presente=False, vendor=self.SIGNATURE.vendor, state=State.NOT_PRESENT)

        res = DetectionResult(
            presente=True,
            vendor=self.SIGNATURE.vendor,
            variant=self.variante(ctx),
            modality=self.modalidade(ctx),
            sitekey=self.sitekey(ctx),
        )

        containers = ctx.containers(self)
        campos = ctx.campos(self)
        frames = ctx.frames(self)
        scripts = ctx.scripts(self)
        globais = ctx.globais(self)
        customs = self._custom_elements(ctx)
        eventos = ctx.eventos

        if containers:
            res.container_selector = containers[0].get("seletor")
            res.com(Evidence(Signal.CONTAINER, containers[0].get("seletor") or "", origem="dom"))
            if res.sitekey:
                res.com(Evidence(Signal.SITEKEY_ATTR, "presente", origem="dom"))
        if scripts:
            res.com(Evidence(Signal.SCRIPT_PRESENT, scripts[0].get("src", "")[:80], origem="dom"))
        if globais:
            res.com(Evidence(Signal.GLOBAL_OBJECT, ",".join(sorted(globais)), origem="dom"))
        if customs:
            res.com(Evidence(Signal.CUSTOM_ELEMENT, customs[0].get("nome", ""), origem="dom"))

        # --- campos de resposta ------------------------------------------- #
        preenchido = None
        for f in campos:
            res.response_field = res.response_field or f.get("nome") or f.get("id")
            res.com(Evidence(Signal.RESPONSE_FIELD_PRESENT, f.get("nome") or "", origem="token"))
            if campo_preenchido(f):
                preenchido = f
                res.com(Evidence(Signal.RESPONSE_FIELD_FILLED,
                                 f"len={tamanho_do_campo(f)} prefixo={prefixo_do_campo(f)}",
                                 positiva=True, origem="token"))
            else:
                res.com(Evidence(Signal.RESPONSE_FIELD_EMPTY, f.get("nome") or "", origem="token"))

        # --- frames: informam, nunca decidem sozinhos --------------------- #
        for i in frames:
            res.frame_url = res.frame_url or i.get("src")
            res.com(Evidence(Signal.IFRAME_PRESENT, i.get("src", "")[:80], origem="dom"))
            if i.get("zero"):
                res.com(Evidence(Signal.IFRAME_ZERO_SIZE, "0x0 temporário", origem="dom"))
            elif not i.get("visivel"):
                res.com(Evidence(Signal.IFRAME_HIDDEN, "oculto", origem="dom"))

        # --- shadow DOM ---------------------------------------------------- #
        if any(c.get("temShadowAberto") for c in containers) or any(
            e.get("temShadowAberto") for e in customs
        ):
            res.com(Evidence(Signal.SHADOW_ROOT_OPEN, "widget em shadow root aberto", origem="dom"))
        suspeitos = ctx.retrato.get("shadowSuspeitos") or []
        if suspeitos and not frames:
            res.com(Evidence(Signal.SHADOW_ROOT_CLOSED_SUSPECT,
                             f"{len(suspeitos)} elemento(s) com área e sem conteúdo legível",
                             origem="dom"))

        # --- callbacks ----------------------------------------------------- #
        for cb in ctx.retrato.get("callbacksRegistrados", []):
            if cb.get("existe"):
                res.com(Evidence(Signal.CALLBACK_REGISTERED, cb.get("nome", ""), origem="dom"))

        # --- popup --------------------------------------------------------- #
        if eventos.get("popups") or ctx.retrato.get("janelas_novas"):
            res.com(Evidence(Signal.POPUP_OPENED, "desafio possivelmente em outra janela",
                             origem="cdp"))

        # --- token fora do DOM (cookie/storage) --------------------------- #
        token_externo = self._token_externo(ctx)
        if token_externo:
            res.com(Evidence(Signal.TOKEN_COOKIE if token_externo[0] == "cookie" else Signal.TOKEN_STORAGE,
                             token_externo[1], positiva=True, origem="token"))

        # --- diagnóstico de ambiente: CSP, rede, JS ----------------------- #
        estado_falha = self._diagnosticar(ctx, res)
        if estado_falha:
            res.state = estado_falha
            return res

        # --- decisão ------------------------------------------------------- #
        if preenchido or token_externo:
            res.state = State.TOKEN_GENERATED
            return res

        # Daqui para baixo: NÃO há evidência positiva. Tudo é pendente, e a
        # granularidade só serve para diagnóstico e escolha de timeout.
        if not (containers or frames or customs or campos):
            # Só script e/ou global, NADA instanciado: nenhum container, nenhum
            # iframe, nenhum campo de resposta.
            #
            # Medido em produção: o portal carrega o
            # api.js, expõe `grecaptcha`, e o próprio JS dele tem
            # `function executarReCaptcha(){ if (false) { grecaptcha.execute(); …`
            # — o captcha está cabeado e DESLIGADO. Não existe sitekey no HTML.
            # Tratar isso como desafio pendente custou 25 s por item e devolveu
            # falha numa página sem captcha.
            #
            # Continua SCRIPT_LOADING aqui (pode ser corrida de carregamento, e
            # concluir ausência agora seria o FN-1 de novo); quem decide é
            # `wait_until_ready`, depois de dar a janela de render.
            morto = ctx.retrato.get("captchaDesativado") or ""
            if morto:
                # O JS do portal guarda a chamada atrás de `if (false)`: não é
                # corrida de carregamento, é captcha desligado. Concluir aqui
                # economiza a janela inteira de render por item.
                res.state = State.NOT_PRESENT
                res.diagnostico = (
                    "o próprio JS do portal mantém a chamada do captcha "
                    f"desativada: `{morto}`. Não há desafio nesta página.")
                res.com(Evidence(Signal.SCRIPT_PRESENT, "chamada atrás de if(false)",
                                 origem="dom"))
                return res
            res.state = State.SCRIPT_LOADING
            res.diagnostico = res.diagnostico or (
                "fornecedor carregado mas nenhum widget instanciado "
                "(sem container, sem iframe, sem campo de resposta)")
        elif (containers or customs) and not frames:
            # O caso canônico do bug: container existe, campo vazio, sem iframe.
            res.state = State.WAITING_RENDER if self._parece_nao_renderizado(containers, customs) else State.PENDING
        elif frames and not any(f.get("visivel") for f in frames):
            # Iframe 0x0 ou oculto: pode estar subindo, pode ser invisible mode.
            res.state = State.WAITING_RENDER
        else:
            res.state = State.PENDING
        return res

    def _parece_nao_renderizado(self, containers, customs) -> bool:
        """Container sem filhos e sem HTML dentro = widget ainda não montou."""
        for c in containers:
            if c.get("filhos", 0) == 0 and c.get("htmlLen", 0) <= 8:
                return True
        for e in customs:
            if not e.get("definido"):
                return True
        return False

    def _token_externo(self, ctx: Ctx) -> tuple[str, str] | None:
        """Token guardado em cookie ou storage pelo próprio fornecedor."""
        for chave in self.SIGNATURE.token_keys:
            for nome in ctx.retrato.get("cookiesNomes", []):
                if chave in nome:
                    return ("cookie", nome)
            for item in ctx.retrato.get("storage", []):
                if chave in item.get("chave", "") and item.get("len", 0) > 0:
                    return ("storage", item["chave"])
        return None

    def _diagnosticar(self, ctx: Ctx, res: DetectionResult) -> State | None:
        """CSP / rede / JS. Retorna estado de falha ou None."""
        eventos = ctx.eventos
        hosts = self.SIGNATURE.script_hosts + self.SIGNATURE.frame_hosts

        for v in eventos.get("csp", []):
            alvo = v.get("bloqueado", "")
            if not hosts or any(h in alvo for h in hosts):
                res.com(Evidence(Signal.CSP_VIOLATION, f"{v.get('diretiva')} bloqueou {alvo[:60]}",
                                 origem="console"))
                res.diagnostico = (
                    f"CSP da página barrou {alvo[:80]} via {v.get('diretiva')}. "
                    "Não é falha de rede nem de bot: é política da própria página."
                )
                return State.CSP_BLOCKED if "frame" not in (v.get("diretiva") or "") else State.FRAME_BLOCKED

        for e in eventos.get("erros", []):
            url = e.get("url", "")
            if e.get("tipo") == "subrecurso" and (not hosts or any(h in url for h in hosts)):
                res.com(Evidence(Signal.SCRIPT_FAILED, url[:80], origem="console"))
                res.diagnostico = (
                    f"Recurso do fornecedor não carregou: {url[:80]}. "
                    "Verificar DNS, proxy corporativo e bloqueio de saída."
                )
                return State.NETWORK_ERROR
            if e.get("tipo") in ("js", "promise"):
                res.com(Evidence(Signal.CONSOLE_ERROR, (e.get("msg") or "")[:80], origem="console"))

        # Reconstrução retroativa: se o recorder entrou depois do load, os
        # eventos do load já passaram. Resource Timing ainda conta o que houve —
        # MAS só quando não há prova de que o script rodou.
        #
        # Motivo, medido num reCAPTCHA de produção: script de fornecedor é
        # cross-origin sem Timing-Allow-Origin, então o Resource Timing devolve
        # tudo opaco (responseStatus 0, bytes 0) mesmo tendo carregado
        # perfeitamente. Ler isso como falha reprovava página boa. Global exposta
        # ou iframe do fornecedor no DOM provam execução e vencem o timing.
        executou = bool(ctx.globais(self) or ctx.frames(self))
        for r in ([] if executou else ctx.retrato.get("recursos", [])):
            url = r.get("url", "")
            if hosts and not any(h in url for h in hosts):
                continue
            if r.get("destino") == "abortado":
                res.com(Evidence(Signal.SCRIPT_FAILED, url[:80], origem="network"))
                res.diagnostico = (
                    f"Requisição do fornecedor sem resposta: {url[:80]} "
                    f"({r.get('detalhe')}), e nenhuma global nem iframe do "
                    "fornecedor no DOM. Verificar DNS, proxy e bloqueio de saída."
                )
                return State.NETWORK_ERROR
            if r.get("destino") == "nunca_buscado":
                res.com(Evidence(Signal.INIT_REQUEST_FAILED, url[:80], origem="network"))
                res.diagnostico = (
                    f"Script {url[:80]} está no DOM mas nunca foi buscado: "
                    "bloqueio antes da requisição (CSP da página ou extensão/"
                    "política do browser). Não é falha de rede."
                )
                return State.CSP_BLOCKED

        # Contexto não-seguro: vários fornecedores simplesmente não sobem em HTTP.
        if ctx.retrato.get("origemSegura") is False and ctx.retrato.get("protocolo") == "http:":
            res.diagnostico = (
                "Página em HTTP (contexto não-seguro). Fornecedores de CAPTCHA "
                "exigem HTTPS; o widget não vai inicializar."
            )
            res.com(Evidence(Signal.INIT_REQUEST_FAILED, "contexto não-seguro", origem="dom"))
            return State.NETWORK_ERROR
        return None

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    def inspect_state(self, ctx: Ctx) -> DetectionResult:
        return self.detect(ctx)

    def wait_until_ready(self, ctx_factory, timeout: float | None = None) -> DetectionResult:
        """
        Espera o widget sair de SCRIPT_LOADING/WAITING_RENDER. Não usa sleep de
        transição: reavalia o retrato, que é reativo ao MutationObserver.
        """
        cfg = CONFIG
        limite = time.monotonic() + (timeout or cfg.timeouts.render_s)
        ultimo = self.detect(ctx_factory())
        while time.monotonic() < limite:
            if ultimo.state not in (State.SCRIPT_LOADING, State.WAITING_RENDER):
                return ultimo
            time.sleep(cfg.timeouts.poll_s)
            ultimo = self.detect(ctx_factory())
        if ultimo.state in (State.SCRIPT_LOADING, State.WAITING_RENDER):
            if self._nada_instanciado(ctx_factory()):
                # Passou a janela inteira e o fornecedor nunca instanciou nada.
                # Isto NÃO é o FN-1: lá havia container e campo de resposta vazio
                # (widget existindo e pendente). Aqui não há container, nem
                # iframe, nem campo — não há desafio para resolver, e o caso
                # medido é o portal que deixa a chamada atrás de `if (false)`.
                ultimo.state = State.NOT_PRESENT
                ultimo.diagnostico = (
                    "script do fornecedor carregado, mas nenhum widget foi "
                    "instanciado na janela de render: sem container, sem iframe e "
                    "sem campo de resposta. Medido num portal real, onde ele "
                    "mantém a chamada do captcha desativada no próprio JS "
                    "(`if (false)`). Não há desafio a resolver nesta página."
                )
                # Variante ligada a um gesto do site (botão) é o MESMO estado de
                # DOM de uma página sem captcha — e aqui a conclusão "não há
                # desafio" engana: o widget existe, só monta depois do clique.
                # Medido: MEXC (geetest_v4/bind) e bilibili (geetest_v3/bind)
                # ficam assim ao carregar; o hCaptcha invisível da Receita é o
                # mesmo caso. Só o texto muda — o estado segue NOT_PRESENT,
                # porque não há nada a resolver ENQUANTO o gesto não acontece.
                if ultimo.variant in (Variant.BIND, Variant.INVISIBLE):
                    ultimo.diagnostico = (
                        f"{ultimo.vendor.value} ({ultimo.variant.value}): o widget está "
                        "ligado a um gesto do próprio site (botão de enviar/entrar) e "
                        "não monta sozinho. Nada a resolver AGORA — chame resolver() "
                        "depois do clique do site, nunca antes. Se você chamou antes, "
                        "este NOT_PRESENT não significa 'página sem captcha'."
                    )
                return ultimo
            ultimo.state = State.TIMEOUT
            ultimo.diagnostico = ultimo.diagnostico or (
                "Widget não renderizou na janela configurada. Continua PENDENTE, "
                "não ausente: há container e campo de resposta vazio."
            )
        return ultimo

    def _nada_instanciado(self, ctx: Ctx) -> bool:
        """True se o fornecedor não tem NADA no DOM além de script/global."""
        if self.SEM_WIDGET_POR_DESIGN:
            return False
        return not (ctx.containers(self) or ctx.frames(self)
                    or ctx.campos(self) or self._custom_elements(ctx))

    def motivo_bloqueio(self, res: DetectionResult) -> str:
        """
        Por que o caminho automático não conclui ESTE desafio. Vai para o
        `motivo` do resultado e para o log — é o que o RPA lê para decidir se
        reenfileira, pula ou avisa alguém.
        """
        return (
            f"{res.vendor.value} ({res.variant.value}): o token só é emitido depois de "
            "resolver o desafio visual, e a resposta não está no DOM."
        )

    def wait_for_completion(self, ctx_factory, timeout: float | None = None) -> CaptchaResult:
        cfg = CONFIG
        limite = time.monotonic() + (timeout or cfg.timeouts.validacao_s)
        inicio = time.monotonic()
        while time.monotonic() < limite:
            res = self.detect(ctx_factory())
            if res.state in (State.TOKEN_GENERATED, State.VALIDATED, State.CALLBACK_EXECUTED):
                return CaptchaResult(
                    sucesso=True,
                    state=res.state,
                    vendor=res.vendor,
                    duracao_s=round(time.monotonic() - inicio, 2),
                    resolvido_por="automatico_pow" if self.AUTORRESOLVE else "automatico_confianca",
                    evidencias_positivas=[e for e in res.evidencias if e.positiva],
                )
            if res.state in (State.CSP_BLOCKED, State.FRAME_BLOCKED, State.NETWORK_ERROR):
                return CaptchaResult(sucesso=False, state=res.state, vendor=res.vendor,
                                     duracao_s=round(time.monotonic() - inicio, 2),
                                     motivo=res.diagnostico)
            time.sleep(cfg.timeouts.poll_s)
        return CaptchaResult(sucesso=False, state=State.TIMEOUT, vendor=self.SIGNATURE.vendor,
                             duracao_s=round(time.monotonic() - inicio, 2),
                             motivo="tempo esgotado aguardando conclusão")

    def validate_completion(self, ctx: Ctx) -> CaptchaResult:
        """
        Exige evidência POSITIVA. Checkbox marcado, iframe que sumiu, botão
        habilitado e título de página trocado não valem — nenhum deles prova
        que existe token válido.
        """
        res = self.detect(ctx)
        positivas = [e for e in res.evidencias if e.positiva]
        if not positivas:
            return CaptchaResult(sucesso=False, state=res.state, vendor=res.vendor,
                                 motivo="sem evidência positiva: campo de resposta vazio "
                                        "e nenhum token em cookie/storage")
        return CaptchaResult(sucesso=True, state=res.state, vendor=res.vendor,
                             resolvido_por="automatico_pow" if self.AUTORRESOLVE else "automatico_confianca",
                             evidencias_positivas=positivas)
