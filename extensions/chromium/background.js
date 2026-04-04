const AI_HELPER_BACKGROUND_MESSAGE = "AI_HELPER_BACKGROUND_REQUEST";
const AI_HELPER_CONTENT_MESSAGE = "AI_HELPER_CONTENT_REQUEST";

const draftActions = new Map();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== AI_HELPER_BACKGROUND_MESSAGE) {
        return false;
    }

    handleBackgroundRequest(message.payload, sender)
        .then(sendResponse)
        .catch((error) => {
            sendResponse(buildErrorEnvelope(message.payload, "INTERNAL", error.message, true, 0));
        });

    return true;
});

async function handleBackgroundRequest(envelope, sender) {
    const startedAt = Date.now();

    if (!isObject(envelope)) {
        return buildErrorEnvelope({}, "INTERNAL", "Некорректный envelope.", false, 0);
    }

    if (envelope.tool === "browser.message.send") {
        const response = await handleMessageSend(envelope, sender);
        response.duration_ms = Date.now() - startedAt;
        return response;
    }

    const tab = await resolveActiveTab(sender);
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
        type: AI_HELPER_CONTENT_MESSAGE,
        payload: envelope,
    });

    if (response?.ok && envelope.tool === "browser.message.draft" && response.output?.draft_ready) {
        draftActions.set(response.output.action_id, {
            tabId: tab.id,
            trace_id: envelope.trace_id,
            session_id: response.session_id,
        });
    }

    response.duration_ms = Date.now() - startedAt;
    return response;
}

async function handleMessageSend(envelope, sender) {
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
        type: AI_HELPER_CONTENT_MESSAGE,
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
            type: AI_HELPER_CONTENT_MESSAGE,
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
