const MESSAGE_TYPES = {
    popupRun: "AI_HELPER_POPUP_RUN",
    popupGetBridgeState: "AI_HELPER_POPUP_GET_BRIDGE_STATE",
    popupSaveBridgeConfig: "AI_HELPER_POPUP_SAVE_BRIDGE_CONFIG",
    contentRequest: "AI_HELPER_CONTENT_REQUEST",
};

const STORAGE_KEYS = {
    bridgeConfig: "ai_helper_bridge_config",
};

const DEFAULT_BRIDGE_CONFIG = {
    mode: "manual",
    backendUrl: "",
    apiKey: "",
    forwardResponsesToBackend: false,
};

const draftActions = new Map();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message?.type) {
        case MESSAGE_TYPES.popupRun:
            handlePopupRun(message.payload, sender)
                .then(sendResponse)
                .catch((error) => {
                    sendResponse(buildErrorEnvelope(message.payload, "INTERNAL", error.message, true, 0));
                });
            return true;
        case MESSAGE_TYPES.popupGetBridgeState:
            getBridgeState()
                .then(sendResponse)
                .catch((error) => {
                    sendResponse({
                        ok: false,
                        error: error.message,
                    });
                });
            return true;
        case MESSAGE_TYPES.popupSaveBridgeConfig:
            saveBridgeConfig(message.payload)
                .then(sendResponse)
                .catch((error) => {
                    sendResponse({
                        ok: false,
                        error: error.message,
                    });
                });
            return true;
        default:
            return false;
    }
});

async function handlePopupRun(envelope, sender) {
    return dispatchEnvelope(envelope, {
        source: "popup",
        sender,
    });
}

async function dispatchEnvelope(envelope, context = {}) {
    const startedAt = Date.now();
    const bridgeConfig = await loadBridgeConfig();

    if (!isObject(envelope)) {
        return buildErrorEnvelope({}, "INTERNAL", "Некорректный envelope.", false, 0);
    }

    if (bridgeConfig.mode === "backend") {
        // Placeholder for future backend transport.
        // The execution contract already goes through this dispatcher, so
        // swapping transports later will not affect popup/content logic.
    }

    const response = await executeEnvelopeLocally(envelope, context);
    response.duration_ms = Date.now() - startedAt;

    if (bridgeConfig.forwardResponsesToBackend && bridgeConfig.backendUrl) {
        void forwardResponseToBackend(bridgeConfig, response);
    }

    return response;
}

async function executeEnvelopeLocally(envelope, context = {}) {
    if (envelope.tool === "browser.message.send") {
        return handleMessageSend(envelope);
    }

    const tab = await resolveActiveTab(context.sender);
    if (!tab?.id) {
        return buildErrorEnvelope(envelope, "NAVIGATION_FAILED", "Не удалось определить активную вкладку.", true, 0);
    }

    if (isRestrictedUrl(tab.url)) {
        return buildErrorEnvelope(
            envelope,
            "NAVIGATION_FAILED",
            "Текущая вкладка недоступна для расширения. Открой обычный сайт.",
            false,
            0
        );
    }

    await ensureContentScript(tab.id);

    const response = await chrome.tabs.sendMessage(tab.id, {
        type: MESSAGE_TYPES.contentRequest,
        payload: envelope,
    });

    if (response?.ok && envelope.tool === "browser.message.draft" && response.output?.draft_ready) {
        draftActions.set(response.output.action_id, {
            tabId: tab.id,
            trace_id: envelope.trace_id,
            session_id: response.session_id,
        });
    }

    return response;
}

async function handleMessageSend(envelope) {
    const actionId = envelope?.input?.action_id;
    if (!actionId) {
        return buildErrorEnvelope(envelope, "INTERNAL", "Не передан action_id.", false, 0);
    }

    if (!envelope?.input?.confirm) {
        return buildErrorEnvelope(envelope, "CONFIRMATION_REQUIRED", "Для отправки нужен confirm=true.", false, 0);
    }

    const action = draftActions.get(actionId);
    if (!action) {
        return buildErrorEnvelope(
            envelope,
            "ELEMENT_NOT_FOUND",
            "Черновик не найден или уже истёк.",
            false,
            0
        );
    }

    await ensureContentScript(action.tabId);

    const response = await chrome.tabs.sendMessage(action.tabId, {
        type: MESSAGE_TYPES.contentRequest,
        payload: {
            ...envelope,
            input: {
                ...envelope.input,
                action_id: actionId,
            },
        },
    });

    if (response?.ok) {
        draftActions.delete(actionId);
    }

    if (!response?.session_id && action.session_id) {
        response.session_id = action.session_id;
    }

    return response;
}

async function resolveActiveTab(sender) {
    if (sender?.tab?.id) {
        return sender.tab;
    }

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab;
}

async function ensureContentScript(tabId) {
    try {
        await chrome.tabs.sendMessage(tabId, {
            type: MESSAGE_TYPES.contentRequest,
            payload: { tool: "browser.ping", input: {} },
        });
    } catch (error) {
        if (!String(error?.message || "").includes("Receiving end does not exist")) {
            throw error;
        }

        await chrome.scripting.executeScript({
            target: { tabId },
            files: ["contentScript.js"],
        });
    }
}

async function getBridgeState() {
    const bridgeConfig = await loadBridgeConfig();
    return {
        ok: true,
        config: bridgeConfig,
        draft_actions_count: draftActions.size,
    };
}

async function loadBridgeConfig() {
    const stored = await chrome.storage.local.get(STORAGE_KEYS.bridgeConfig);
    return {
        ...DEFAULT_BRIDGE_CONFIG,
        ...(stored?.[STORAGE_KEYS.bridgeConfig] || {}),
    };
}

async function saveBridgeConfig(configPatch) {
    if (!isObject(configPatch)) {
        return {
            ok: false,
            error: "Некорректный bridge config.",
        };
    }

    const current = await loadBridgeConfig();
    const nextConfig = {
        ...current,
        ...configPatch,
    };

    await chrome.storage.local.set({
        [STORAGE_KEYS.bridgeConfig]: nextConfig,
    });

    return {
        ok: true,
        config: nextConfig,
    };
}

async function forwardResponseToBackend(bridgeConfig, response) {
    try {
        await fetch(bridgeConfig.backendUrl, {
            method: "POST",
            headers: buildBackendHeaders(bridgeConfig),
            body: JSON.stringify(response),
        });
    } catch (_error) {
        // Keep manual testing resilient even when backend is unavailable.
    }
}

function buildBackendHeaders(bridgeConfig) {
    const headers = {
        "Content-Type": "application/json",
    };

    if (bridgeConfig.apiKey) {
        headers.Authorization = `Bearer ${bridgeConfig.apiKey}`;
    }

    return headers;
}

function buildErrorEnvelope(envelope, code, message, retryable, durationMs) {
    return {
        trace_id: envelope?.trace_id || crypto.randomUUID(),
        session_id: envelope?.session_id || null,
        tool: envelope?.tool || "unknown",
        ok: false,
        output: {},
        error: {
            code,
            message,
            retryable,
        },
        duration_ms: durationMs,
    };
}

function isRestrictedUrl(url) {
    return /^(chrome|edge|yandex|about|chrome-extension):\/\//i.test(url || "");
}

function isObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
