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
    bridgeWebSocketUrl: "ws://127.0.0.1:8080/bridge/ws",
    autoConnectBridge: true,
};

const draftActions = new Map();
const browserSessions = new Map();
const bridgeState = {
    socket: null,
    connected: false,
    connecting: false,
    lastError: "",
    reconnectTimer: null,
};

chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create("ai-helper-bridge-reconnect", { periodInMinutes: 1 });
    void initializeBridge();
});

chrome.runtime.onStartup.addListener(() => {
    chrome.alarms.create("ai-helper-bridge-reconnect", { periodInMinutes: 1 });
    void initializeBridge();
});

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm?.name === "ai-helper-bridge-reconnect") {
        void initializeBridge();
    }
});

chrome.tabs.onRemoved.addListener((tabId) => {
    for (const [sessionId, session] of browserSessions.entries()) {
        if (session?.tabId === tabId) {
            browserSessions.delete(sessionId);
        }
    }
});

void initializeBridge();

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
                .then(async (response) => {
                    await initializeBridge(true);
                    sendResponse(response);
                })
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

async function initializeBridge(forceReconnect = false) {
    const config = await loadBridgeConfig();
    if (!config.autoConnectBridge || !config.bridgeWebSocketUrl) {
        disconnectBridge();
        return;
    }

    if (forceReconnect) {
        disconnectBridge();
    }

    connectBridge(config);
}

function connectBridge(config) {
    if (bridgeState.connected || bridgeState.connecting) {
        return;
    }

    bridgeState.connecting = true;
    bridgeState.lastError = "";

    try {
        const socket = new WebSocket(config.bridgeWebSocketUrl);
        bridgeState.socket = socket;

        socket.onopen = () => {
            bridgeState.connected = true;
            bridgeState.connecting = false;
            bridgeState.lastError = "";
            sendBridgeMessage({
                type: "hello",
                client_id: chrome.runtime.id,
            });
        };

        socket.onmessage = (event) => {
            void handleBridgeMessage(event.data);
        };

        socket.onerror = () => {
            bridgeState.lastError = "bridge socket error";
        };

        socket.onclose = () => {
            bridgeState.connected = false;
            bridgeState.connecting = false;
            bridgeState.socket = null;
            scheduleReconnect();
        };
    } catch (error) {
        bridgeState.connected = false;
        bridgeState.connecting = false;
        bridgeState.lastError = error.message;
        scheduleReconnect();
    }
}

function disconnectBridge() {
    if (bridgeState.reconnectTimer) {
        clearTimeout(bridgeState.reconnectTimer);
        bridgeState.reconnectTimer = null;
    }

    if (bridgeState.socket) {
        bridgeState.socket.close();
    }

    bridgeState.socket = null;
    bridgeState.connected = false;
    bridgeState.connecting = false;
}

function scheduleReconnect() {
    if (bridgeState.reconnectTimer) {
        clearTimeout(bridgeState.reconnectTimer);
    }

    bridgeState.reconnectTimer = setTimeout(() => {
        bridgeState.reconnectTimer = null;
        void initializeBridge();
    }, 2000);
}

async function handleBridgeMessage(rawData) {
    let message;
    try {
        message = JSON.parse(rawData);
    } catch (_error) {
        return;
    }

    if (message?.type === "ping") {
        sendBridgeMessage({
            type: "pong",
            client_id: chrome.runtime.id,
        });
        return;
    }

    if (message?.type === "bridge_state") {
        bridgeState.lastError = "";
        return;
    }

    if (message?.type !== "tool_request" || !message.request) {
        return;
    }

    const response = await dispatchEnvelope(message.request, {
        source: "bridge",
    });

    sendBridgeMessage({
        type: "tool_response",
        client_id: chrome.runtime.id,
        response,
    });
}

function sendBridgeMessage(message) {
    const socket = bridgeState.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
    }

    socket.send(JSON.stringify(message));
}

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

    const response = await executeEnvelopeLocally(envelope, context);
    response.duration_ms = Date.now() - startedAt;

    if (bridgeConfig.forwardResponsesToBackend && bridgeConfig.backendUrl) {
        void forwardResponseToBackend(bridgeConfig, response);
    }

    return response;
}

async function executeEnvelopeLocally(envelope, context = {}) {
    if (envelope.tool === "browser.open") {
        return handleBrowserOpen(envelope);
    }

    if (envelope.tool === "browser.message.send") {
        return handleMessageSend(envelope);
    }

    const tab = await resolveTargetTab(envelope, context.sender);
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
    const response = await sendEnvelopeToTab(tab.id, envelope);

    if (response?.ok && envelope.tool === "browser.message.draft" && response.output?.draft_ready) {
        draftActions.set(response.output.action_id, {
            tabId: tab.id,
            trace_id: envelope.trace_id,
            session_id: response.session_id,
        });
    }

    return response;
}

async function handleBrowserOpen(envelope) {
    const input = envelope?.input || {};
    const url = String(input.url || "").trim();
    const sessionId = envelope?.session_id || crypto.randomUUID();

    if (!/^https?:\/\//i.test(url)) {
        return buildErrorEnvelope(envelope, "NAVIGATION_FAILED", "Не передан корректный input.url.", false, 0);
    }

    const activate = input.activate === true;
    let tab = null;
    const existingSession = browserSessions.get(sessionId);

    if (existingSession?.tabId) {
        try {
            tab = await chrome.tabs.update(existingSession.tabId, {
                url,
                active: activate,
            });
        } catch (_error) {
            browserSessions.delete(sessionId);
        }
    }

    if (!tab) {
        tab = await chrome.tabs.create({ url, active: activate });
    }

    if (tab?.id) {
        await waitForTabComplete(tab.id, 15000);
        browserSessions.set(sessionId, {
            tabId: tab.id,
            url,
            updatedAt: Date.now(),
        });
    }

    return {
        trace_id: envelope?.trace_id || crypto.randomUUID(),
        session_id: sessionId,
        tool: envelope?.tool || "browser.open",
        ok: true,
        output: {
            opened: true,
            url,
            tab_id: tab?.id || null,
        },
        error: null,
        duration_ms: 0,
    };
}

function waitForTabComplete(tabId, timeoutMs) {
    return new Promise((resolve) => {
        let finished = false;

        const done = () => {
            if (finished) {
                return;
            }
            finished = true;
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timer);
            resolve();
        };

        const onUpdated = (updatedTabId, changeInfo) => {
            if (updatedTabId !== tabId) {
                return;
            }
            if (changeInfo.status === "complete") {
                done();
            }
        };

        const timer = setTimeout(done, timeoutMs);

        chrome.tabs.get(tabId)
            .then((tab) => {
                if (tab?.status === "complete") {
                    done();
                    return;
                }
                chrome.tabs.onUpdated.addListener(onUpdated);
            })
            .catch(() => {
                chrome.tabs.onUpdated.addListener(onUpdated);
            });
    });
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
    const response = await sendEnvelopeToTab(action.tabId, {
        ...envelope,
        input: {
            ...envelope.input,
            action_id: actionId,
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

async function resolveTargetTab(envelope, sender) {
    const sessionId = envelope?.session_id;
    if (sessionId && browserSessions.has(sessionId)) {
        const session = browserSessions.get(sessionId);
        if (session?.tabId) {
            try {
                const tab = await chrome.tabs.get(session.tabId);
                if (tab?.id) {
                    return tab;
                }
            } catch (_error) {
                browserSessions.delete(sessionId);
            }
        }
    }

    return resolveActiveTab(sender);
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
        await wait(300);
    }
}

async function sendEnvelopeToTab(tabId, envelope) {
    let lastError = null;

    for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
            return await chrome.tabs.sendMessage(tabId, {
                type: MESSAGE_TYPES.contentRequest,
                payload: envelope,
            });
        } catch (error) {
            lastError = error;
            try {
                await ensureContentScript(tabId);
            } catch (_injectError) {
                if (attempt === 3) {
                    throw error;
                }
            }
            await wait(250 * attempt);
        }
    }

    throw lastError || new Error("Failed to deliver message to content script.");
}

async function getBridgeState() {
    if (!bridgeState.connected && !bridgeState.connecting) {
        await initializeBridge();
    }

    const bridgeConfig = await loadBridgeConfig();
    return {
        ok: true,
        config: bridgeConfig,
        draft_actions_count: draftActions.size,
        connected: bridgeState.connected,
        connecting: bridgeState.connecting,
        last_error: bridgeState.lastError,
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

function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
