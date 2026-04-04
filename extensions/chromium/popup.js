const AI_HELPER_BACKGROUND_MESSAGE = "AI_HELPER_BACKGROUND_REQUEST";

const commandInput = document.getElementById("commandInput");
const sendBtn = document.getElementById("sendBtn");
const diagnoseBtn = document.getElementById("diagnoseBtn");
const vkDebugBtn = document.getElementById("vkDebugBtn");
const statusBox = document.getElementById("statusBox");
const exampleButtons = document.querySelectorAll(".exampleBtn");

const examplePayloads = {
    search: {
        trace_id: crypto.randomUUID(),
        session_id: null,
        tool: "browser.search",
        input: {
            query: "Найди iPhone 256GB новый",
            engine: "yandex",
            limit: 5,
        },
    },
    extract: {
        trace_id: crypto.randomUUID(),
        session_id: null,
        tool: "browser.extract",
        input: {
            schema: { type: "product", fields: ["title", "price", "currency", "url"] },
            mode: "dom_first",
            limit: 5,
        },
    },
    draft: {
        trace_id: crypto.randomUUID(),
        session_id: null,
        tool: "browser.message.draft",
        input: {
            destination_hint: "Серёжа Лазуренко",
            message_text: "Привет",
        },
    },
    send: {
        trace_id: crypto.randomUUID(),
        session_id: null,
        tool: "browser.message.send",
        input: {
            action_id: "paste-action-id-here",
            confirm: true,
        },
    },
};

function setStatus(message, type = "info") {
    statusBox.textContent = message;
    statusBox.dataset.type = type;
}

async function runEnvelope() {
    const rawText = commandInput.value.trim();
    if (!rawText) {
        setStatus("Вставь JSON-envelope для теста.", "error");
        return;
    }

    let envelope;
    try {
        envelope = JSON.parse(rawText);
    } catch (error) {
        setStatus(`JSON не распарсился.\n\n${error.message}`, "error");
        return;
    }

    setStatus("Отправляю envelope в background dispatcher...", "pending");

    try {
        const response = await chrome.runtime.sendMessage({
            type: AI_HELPER_BACKGROUND_MESSAGE,
            payload: envelope,
        });

        if (!response) {
            setStatus("Background не вернул ответ.", "error");
            return;
        }

        setStatus(JSON.stringify(response, null, 2), response.ok ? "success" : "error");

        if (response.tool === "browser.message.draft" && response.ok && response.output?.action_id) {
            examplePayloads.send = {
                trace_id: crypto.randomUUID(),
                session_id: response.session_id,
                tool: "browser.message.send",
                input: {
                    action_id: response.output.action_id,
                    confirm: true,
                },
            };
        }
    } catch (error) {
        setStatus(`Не удалось отправить envelope в background.\n\n${error.message}`, "error");
    }
}

async function runDiagnostics() {
    setStatus("Собираю диагностику страницы...", "pending");

    try {
        const response = await chrome.runtime.sendMessage({
            type: AI_HELPER_BACKGROUND_MESSAGE,
            payload: {
                trace_id: crypto.randomUUID(),
                session_id: null,
                tool: "browser.diagnose",
                input: {},
            },
        });

        setStatus(JSON.stringify(response, null, 2), response.ok ? "success" : "error");
    } catch (error) {
        setStatus(`Не удалось снять диагностику.\n\n${error.message}`, "error");
    }
}

async function runVkDebug() {
    setStatus("Собираю VK debug по результатам поиска...", "pending");

    try {
        const response = await chrome.runtime.sendMessage({
            type: AI_HELPER_BACKGROUND_MESSAGE,
            payload: {
                trace_id: crypto.randomUUID(),
                session_id: null,
                tool: "browser.debug.vk_search",
                input: {},
            },
        });

        setStatus(JSON.stringify(response, null, 2), response.ok ? "success" : "error");
    } catch (error) {
        setStatus(`Не удалось снять VK debug.\n\n${error.message}`, "error");
    }
}

function loadExample(exampleName) {
    const payload = examplePayloads[exampleName];
    if (!payload) {
        return;
    }

    payload.trace_id = crypto.randomUUID();
    commandInput.value = JSON.stringify(payload, null, 2);
    commandInput.focus();
}

sendBtn.addEventListener("click", runEnvelope);
diagnoseBtn.addEventListener("click", runDiagnostics);
vkDebugBtn.addEventListener("click", runVkDebug);

commandInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        runEnvelope();
    }
});

exampleButtons.forEach((button) => {
    button.addEventListener("click", () => {
        loadExample(button.dataset.example);
    });
});
