const AI_HELPER_CONTENT_MESSAGE = "AI_HELPER_CONTENT_REQUEST";
const MAX_RESULTS = 10;

const draftedActions = new Map();
const scannedElements = new Map();

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== AI_HELPER_CONTENT_MESSAGE) {
        return false;
    }

    handleEnvelope(message.payload)
        .then(sendResponse)
        .catch((error) => {
            sendResponse(buildErrorEnvelope(message.payload, "INTERNAL", error.message, true));
        });

    return true;
});

async function handleEnvelope(envelope) {
    const tool = envelope?.tool;

    if (tool === "browser.ping") {
        return {
            trace_id: envelope?.trace_id || crypto.randomUUID(),
            session_id: envelope?.session_id || getSessionId(),
            tool,
            ok: true,
            output: { pong: true },
            error: null,
            duration_ms: 0,
        };
    }

    if (tool === "browser.diagnose") {
        return {
            trace_id: envelope.trace_id,
            session_id: envelope.session_id || getSessionId(),
            tool,
            ok: true,
            output: inspectPage(),
            error: null,
            duration_ms: 0,
        };
    }

    if (tool === "browser.debug.vk_search") {
        return {
            trace_id: envelope.trace_id,
            session_id: envelope.session_id || getSessionId(),
            tool,
            ok: true,
            output: inspectVkSearchResults(),
            error: null,
            duration_ms: 0,
        };
    }

    switch (tool) {
        case "browser.scan":
            return runTool(envelope, handleBrowserScan);
        case "browser.act":
            return runTool(envelope, handleBrowserAct);
        case "browser.search":
            return runTool(envelope, handleBrowserSearch);
        case "browser.extract":
            return runTool(envelope, handleBrowserExtract);
        case "browser.message.draft":
            return runTool(envelope, handleBrowserMessageDraft);
        case "browser.message.send":
            return runTool(envelope, handleBrowserMessageSend);
        default:
            return buildErrorEnvelope(envelope, "INTERNAL", `Неизвестный tool: ${tool}`, false);
    }
}

async function runTool(envelope, handler) {
    const startedAt = performance.now();

    try {
        const output = await handler(envelope.input || {});
        return {
            trace_id: envelope.trace_id || crypto.randomUUID(),
            session_id: envelope.session_id || getSessionId(),
            tool: envelope.tool,
            ok: true,
            output,
            error: null,
            duration_ms: Math.round(performance.now() - startedAt),
        };
    } catch (error) {
        return buildErrorEnvelope(
            envelope,
            error.code || "INTERNAL",
            error.message || "Неизвестная ошибка.",
            error.retryable ?? true,
            error.details || {},
            Math.round(performance.now() - startedAt)
        );
    }
}

async function handleBrowserSearch(input) {
    const query = String(input?.query || "").trim();
    const engine = String(input?.engine || "yandex").toLowerCase();
    const limit = clamp(Number(input?.limit || 5), 1, MAX_RESULTS);

    if (!query) {
        throw createToolError("INTERNAL", "Не передан input.query.", false);
    }

    const hostname = window.location.hostname;
    const onEnginePage =
        (engine === "yandex" && hostname.includes("yandex")) ||
        (engine === "google" && hostname.includes("google"));

    if (!onEnginePage) {
        const targetUrl =
            engine === "google"
                ? `https://www.google.com/search?q=${encodeURIComponent(query)}`
                : `https://yandex.ru/search/?text=${encodeURIComponent(query)}`;

        window.location.href = targetUrl;
        throw createToolError("NAVIGATION_FAILED", "Открыта страница поисковика. Повтори запрос после загрузки.", true);
    }

    const searchInput = findFirstVisible([
        "input[name='text']",
        "input[name='q']",
        "input[aria-label*='Поиск' i]",
        "input[aria-label*='Search' i]",
        "textarea[name='q']",
    ]);

    if (searchInput) {
        focusAndReplace(searchInput, query);
        dispatchEnter(searchInput);
        await wait(1200);
    }

    const selectors =
        engine === "google"
            ? ["#search .g", "[data-snc]"]
            : ["li.serp-item", ".serp-item", ".organic__url-text"];

    const results = collectSearchResults(selectors, limit);
    if (!results.length) {
        throw createToolError("ELEMENT_NOT_FOUND", "Не удалось собрать результаты поиска.", true);
    }

    return { results };
}

async function handleBrowserExtract(input) {
    const schema = input?.schema || {};
    const limit = clamp(Number(input?.limit || 5), 1, MAX_RESULTS);
    const fields = Array.isArray(schema.fields) ? schema.fields : [];

    const cards = collectCandidateCards(limit);
    const schemaType = String(schema.type || "").toLowerCase();
    if (!cards.length && schemaType === "product") {
        return {
            schema,
            mode: input?.mode || "dom_first",
            items: [extractFieldsFromCard(document.body, fields)],
        };
    }
    if (!cards.length) {
        throw createToolError("ELEMENT_NOT_FOUND", "Не удалось найти карточки или структурированные блоки.", true);
    }

    const items = cards.map((card) => extractFieldsFromCard(card, fields));
    return {
        schema,
        mode: input?.mode || "dom_first",
        items,
    };
}

async function handleBrowserScan(input) {
    const limit = clamp(Number(input?.limit || 40), 1, 100);
    const auth = detectAuthRequired();
    const elements = collectInteractiveElements(limit);

    return {
        url: window.location.href,
        title: document.title,
        auth,
        page_text: normalizeText(document.body?.innerText || "").slice(0, 4000),
        elements,
    };
}

async function handleBrowserAct(input) {
    const action = String(input?.action || "").trim().toLowerCase();
    const elementId = String(input?.element_id || "").trim();
    const text = String(input?.text || "");
    const key = String(input?.key || "Enter");

    if (!action) {
        throw createToolError("INTERNAL", "Не передан input.action.", false);
    }

    const beforeState = capturePageState();
    const element = elementId ? scannedElements.get(elementId) || null : null;

    switch (action) {
        case "click": {
            if (!element) {
                throw createToolError("ELEMENT_NOT_FOUND", "Элемент для клика не найден.", false);
            }
            activateElement(element);
            await wait(500);
            break;
        }
        case "type": {
            if (!element) {
                throw createToolError("ELEMENT_NOT_FOUND", "Элемент для ввода не найден.", false);
            }
            focusAndReplace(element, text);
            await wait(250);
            break;
        }
        case "press": {
            const target = element || document.activeElement;
            if (!(target instanceof HTMLElement)) {
                throw createToolError("ELEMENT_NOT_FOUND", "Не найден элемент для нажатия клавиши.", false);
            }
            dispatchKey(target, key);
            await wait(300);
            break;
        }
        default:
            throw createToolError("INTERNAL", `Неизвестное действие: ${action}`, false);
    }

    const navigation = await waitForNavigationOrContentChange(beforeState, 1800);
    if (navigation.changed) {
        await waitForPageToSettle();
    }

    return {
        action,
        element_id: elementId || null,
        navigation,
        page: {
            url: window.location.href,
            title: document.title,
            auth: detectAuthRequired(),
        },
    };
}

async function handleBrowserMessageDraft(input) {
    const target = String(input?.destination_hint || "").trim();
    const messageText = String(input?.message_text || "").trim();
    const authState = detectAuthRequired();

    if (!target) {
        throw createToolError("INTERNAL", "Не передан destination_hint.", false);
    }

    if (!messageText) {
        throw createToolError("INTERNAL", "Не передан message_text.", false);
    }

    if (authState.required) {
        throw createToolError("AUTH_REQUIRED", authState.message, false, {
            site: window.location.hostname,
        });
    }

    const diagnostics = [];
    if (window.location.hostname.includes("vk.com")) {
        return handleVkMessageDraft(target, messageText, diagnostics);
    }

    const searchInput = findFirstVisible([
        "input[type='search']",
        "input[placeholder*='Поиск' i]",
        "input[placeholder*='Search' i]",
        "input[aria-label*='Поиск' i]",
        "input[aria-label*='Search' i]",
    ]);

    if (searchInput) {
        focusAndReplace(searchInput, target);
        diagnostics.push(`Найдено и заполнено поле поиска адресата (${describeElement(searchInput)}).`);
        await wait(500);
    } else {
        diagnostics.push("Поисковое поле не найдено, ищу адресата по тексту.");
    }

    const destinationNode = findElementByText(target);
    if (!destinationNode) {
        throw createToolError("ELEMENT_NOT_FOUND", `Не удалось найти адресата "${target}".`, true, {
            diagnostics,
        });
    }

    const beforeNavigation = capturePageState();
    const clickableDestination = getClickableTarget(destinationNode);
    clickableDestination.click();
    diagnostics.push("Диалог с адресатом открыт.");

    const navigationState = await waitForNavigationOrContentChange(beforeNavigation, 3500);
    if (navigationState.changed) {
        diagnostics.push(
            navigationState.urlChanged
                ? `После клика изменился URL: ${navigationState.fromUrl} -> ${navigationState.toUrl}.`
                : "После клика заметно изменилась DOM-структура страницы."
        );
        await waitForPageToSettle();
    } else {
        diagnostics.push("Явной навигации не было, продолжаю поиск редактора на текущей странице.");
        await wait(1000);
    }

    const editor = await waitForMessageEditor(8, 350);
    if (!editor) {
        throw createToolError("ELEMENT_NOT_FOUND", "Не найдено поле ввода сообщения.", true, {
            diagnostics,
            editorCandidates: collectElementDiagnostics(
                "[contenteditable='true'], [role='textbox'], textarea, input[type='text'], input[type='search']",
                12
            ),
            pageState: capturePageState(),
        });
    }

    focusAndReplace(editor, messageText);
    diagnostics.push(`Сообщение подготовлено в ${describeElement(editor)}.`);
    return buildDraftResult(target, messageText, editor, diagnostics);
}

async function handleVkMessageDraft(target, messageText, diagnostics) {
    const searchInput = await waitForVkMessengerSearchInput(10, 250);
    if (!searchInput) {
        throw createToolError("ELEMENT_NOT_FOUND", "Не удалось найти поиск по чатам VK.", true, {
            diagnostics,
            searchCandidates: collectElementDiagnostics("input, textarea, [role='textbox']", 12),
            pageState: captureVkChatState(),
        });
    }

    const lookupQueries = buildVkLookupQueries(target);
    const searchQuery = lookupQueries[0] || target;
    focusAndReplace(searchInput, searchQuery);
    diagnostics.push(
        searchQuery === target
            ? `VK: заполнен поиск по чатам (${describeElement(searchInput)}).`
            : `VK: поиск по чатам скорректирован до "${searchQuery}" (${describeElement(searchInput)}).`
    );
    await wait(350);

    const activation = await tryActivateVkSearchResult(target, searchInput, diagnostics, lookupQueries);
    if (!activation?.opened) {
        throw createToolError("ELEMENT_NOT_FOUND", `Не удалось открыть чат "${target}" в VK.`, true, {
            diagnostics,
            candidates: collectVkTopCandidateDiagnostics(lookupQueries, searchInput, 8),
            pageState: captureVkChatState(),
        });
    }

    const editor = await waitForMessageEditor(10, 350);
    if (!editor) {
        throw createToolError("ELEMENT_NOT_FOUND", "VK открыл чат, но поле ввода сообщения не найдено.", true, {
            diagnostics,
            editorCandidates: collectElementDiagnostics(
                "[contenteditable='true'], [role='textbox'], textarea, input[type='text'], input[type='search']",
                12
            ),
            pageState: captureVkChatState(),
        });
    }

    focusAndReplace(editor, messageText);
    diagnostics.push(`VK: сообщение подготовлено в ${describeElement(editor)}.`);
    return buildDraftResult(target, messageText, editor, diagnostics, {
        selected_chat: activation.selectedChat || null,
    });
}

function buildDraftResult(target, messageText, editor, diagnostics, extras = {}) {
    const actionId = crypto.randomUUID();
    draftedActions.set(actionId, {
        target,
        messageText,
        createdAt: Date.now(),
        editorDescriptor: describeElement(editor),
        ...extras,
    });

    return {
        draft_ready: true,
        action_id: actionId,
        preview: {
            target,
            message_text: messageText,
        },
        diagnostics,
        ...extras,
    };
}

async function handleBrowserMessageSend(input) {
    const actionId = String(input?.action_id || "").trim();
    const confirm = Boolean(input?.confirm);

    if (!actionId) {
        throw createToolError("INTERNAL", "Не передан action_id.", false);
    }

    if (!confirm) {
        throw createToolError("CONFIRMATION_REQUIRED", "Для отправки нужен confirm=true.", false);
    }

    const draft = draftedActions.get(actionId);
    if (!draft) {
        throw createToolError("ELEMENT_NOT_FOUND", "Черновик не найден на текущей странице.", false);
    }

    const editor = await waitForMessageEditor(4, 250);
    if (!editor) {
        throw createToolError("ELEMENT_NOT_FOUND", "Поле ввода больше недоступно.", true);
    }

    const sendResult = await attemptMessageSend(editor, draft);
    if (!sendResult.sent) {
        throw createToolError("ELEMENT_NOT_FOUND", sendResult.message, true, {
            diagnostics: sendResult.diagnostics,
            pageState: window.location.hostname.includes("vk.com") ? captureVkChatState() : capturePageState(),
        });
    }

    draftedActions.delete(actionId);

    return {
        action_id: actionId,
        status: "sent",
        method: sendResult.method,
        preview: {
            target: draft.target,
            message_text: draft.messageText,
        },
    };
}

async function attemptMessageSend(editor, draft) {
    const diagnostics = [];
    const beforeText = readEditorValue(editor);

    const button = findSendButton(editor);
    if (button) {
        activateElement(button);
        diagnostics.push(`Отправка через кнопку ${describeElement(button)}.`);
        if (await waitForMessageSendEffect(editor, beforeText, draft.messageText, 1200)) {
            return { sent: true, method: "button", diagnostics };
        }
        diagnostics.push("Кнопка была нажата, но редактор не изменился.");
    } else {
        diagnostics.push("Кнопка отправки не найдена, пробую клавиатурные сценарии.");
    }

    for (const strategy of buildSendKeyStrategies()) {
        dispatchKeyStroke(editor, strategy);
        diagnostics.push(`Пробую отправку клавишами: ${describeKeyStrategy(strategy)}.`);
        if (await waitForMessageSendEffect(editor, beforeText, draft.messageText, 900)) {
            return { sent: true, method: describeKeyStrategy(strategy), diagnostics };
        }
    }

    return {
        sent: false,
        method: "",
        message: "Сообщение подготовлено, но я не смог активировать отправку.",
        diagnostics,
    };
}

function findSendButton(editor) {
    if (window.location.hostname.includes("vk.com")) {
        const vkButton = findVkSendButton(editor);
        if (vkButton) {
            return vkButton;
        }
    }

    const attributeSelectors = [
        "button[aria-label*='Отправ' i]",
        "button[title*='Отправ' i]",
        "button[aria-label*='Send' i]",
        "button[title*='Send' i]",
        "[role='button'][aria-label*='Отправ' i]",
        "[role='button'][title*='Отправ' i]",
        "[role='button'][aria-label*='Send' i]",
        "[role='button'][title*='Send' i]",
        "button[data-testid*='send' i]",
        "[role='button'][data-testid*='send' i]",
        "button[class*='send' i]",
        "[role='button'][class*='send' i]",
    ];

    for (const selector of attributeSelectors) {
        const match = Array.from(document.querySelectorAll(selector)).find((element) => isVisible(element));
        if (match) {
            return match;
        }
    }

    return findButtonByText(["Отправить", "Send", "Отослать"], { exact: false });
}

function findVkSendButton(editor) {
    const container = editor.closest("form, footer, [class*='composer'], [class*='im-chat-input'], [class*='Input']");
    const selectors = [
        "button[aria-label*='Отправ' i]",
        "button[title*='Отправ' i]",
        "[role='button'][aria-label*='Отправ' i]",
        "[role='button'][title*='Отправ' i]",
        "button[class*='send' i]",
        "[role='button'][class*='send' i]",
        "button",
        "[role='button']",
    ];

    if (container) {
        for (const selector of selectors) {
            const candidates = Array.from(container.querySelectorAll(selector))
                .filter((element) => isVisible(element))
                .sort((left, right) => scoreSendButtonCandidate(right) - scoreSendButtonCandidate(left));
            if (candidates.length && scoreSendButtonCandidate(candidates[0]) > 0) {
                return candidates[0];
            }
        }
    }

    const globalCandidates = Array.from(document.querySelectorAll("button, [role='button']"))
        .filter((element) => isVisible(element))
        .sort((left, right) => scoreSendButtonCandidate(right) - scoreSendButtonCandidate(left));
    return globalCandidates.length && scoreSendButtonCandidate(globalCandidates[0]) > 0 ? globalCandidates[0] : null;
}

function scoreSendButtonCandidate(element) {
    const text = normalizeText(
        [
            element.innerText,
            element.getAttribute("aria-label"),
            element.getAttribute("title"),
            element.getAttribute("data-testid"),
            element.className,
        ].filter(Boolean).join(" ")
    ).toLowerCase();

    let score = 0;
    if (text.includes("отправ")) {
        score += 80;
    }
    if (text.includes("send")) {
        score += 70;
    }
    if (text.includes("submit")) {
        score += 30;
    }
    if (text.includes("voice") || text.includes("microphone") || text.includes("emoji") || text.includes("стикер")) {
        score -= 120;
    }
    if (element.tagName.toLowerCase() === "button") {
        score += 10;
    }
    return score;
}

function buildSendKeyStrategies() {
    return [
        { key: "Enter" },
        { key: "Enter", ctrlKey: true },
        { key: "Enter", metaKey: true },
    ];
}

function describeKeyStrategy(strategy) {
    const modifiers = [];
    if (strategy.ctrlKey) {
        modifiers.push("Ctrl");
    }
    if (strategy.metaKey) {
        modifiers.push("Meta");
    }
    modifiers.push(strategy.key);
    return modifiers.join("+");
}

function dispatchKeyStroke(element, strategy) {
    dispatchKey(element, strategy.key, strategy);
}

async function waitForMessageSendEffect(editor, beforeText, expectedText, timeoutMs) {
    const startedAt = Date.now();
    const normalizedBefore = normalizeLookupText(beforeText || expectedText || "");

    while (Date.now() - startedAt < timeoutMs) {
        const currentText = normalizeLookupText(readEditorValue(editor));
        if (!currentText) {
            return true;
        }
        if (normalizedBefore && currentText !== normalizedBefore) {
            return true;
        }

        await wait(120);
    }

    return false;
}

function readEditorValue(element) {
    if (!element) {
        return "";
    }
    if (isTextInput(element)) {
        return element.value || "";
    }
    if (element.isContentEditable) {
        return element.textContent || "";
    }
    return element.innerText || "";
}

function inspectPage() {
    return {
        url: window.location.href,
        title: document.title,
        auth: detectAuthRequired(),
        inputs: collectElementDiagnostics("input, textarea, [contenteditable='true'], [role='textbox']", 10),
        buttons: collectElementDiagnostics("button, [role='button']", 12),
        links: collectElementDiagnostics("a", 10),
        lists: collectElementDiagnostics("tr, li, article, div[role='listitem'], .message", 10),
    };
}

function detectAuthRequired() {
    const href = window.location.href.toLowerCase();
    if (/(login|signin|auth|oauth|passport)/i.test(href)) {
        return {
            required: true,
            message: `Я не могу это сделать, пока вы не авторизуетесь на сайте ${window.location.hostname}.`,
        };
    }

    const passwordField = findFirstVisible(["input[type='password']"]);
    if (passwordField) {
        return {
            required: true,
            message: `Я не могу это сделать, пока вы не авторизуетесь на сайте ${window.location.hostname}.`,
        };
    }

    const authFormField = findFirstVisible([
        "input[name*='email' i]",
        "input[name*='login' i]",
        "input[name*='phone' i]",
        "input[autocomplete='username']",
        "input[autocomplete='email']",
        "input[autocomplete='tel']",
    ]);
    const loginButton = findButtonByText(["Войти", "Вход", "Log in", "Login", "Sign in"]);
    const loginLink = findLinkByText(["Войти", "Вход", "Log in", "Login", "Sign in"]);
    if ((loginButton || loginLink) && authFormField) {
        return {
            required: true,
            message: `Я не могу это сделать, пока вы не авторизуетесь на сайте ${window.location.hostname}.`,
        };
    }

    return { required: false, message: "" };
}

function inspectVkSearchResults() {
    const searchInput = findVkMessengerSearchInput();
    const context = findVkSearchContext(searchInput);
    const scope = getVkSearchResultsScope(searchInput);
    const chatSection = findVkChatsSection();

    return {
        url: window.location.href,
        title: document.title,
        searchInput: searchInput ? describeDiagnosticElement(searchInput) : null,
        context: context ? describeDiagnosticElement(context) : null,
        scope: scope ? describeDiagnosticElement(scope) : null,
        chatsSection: chatSection ? describeDiagnosticElement(chatSection) : null,
        candidates: collectVkResultCandidates(scope),
    };
}

function collectInteractiveElements(limit) {
    scannedElements.clear();
    let index = 0;
    const seen = new Set();
    const selectors = [
        "input",
        "textarea",
        "[contenteditable='true']",
        "[role='textbox']",
        "button",
        "a[href]",
        "[role='button']",
        "[role='menuitem']",
        "[role='option']",
        "[tabindex]",
    ];

    const elements = [];
    for (const selector of selectors) {
        for (const element of document.querySelectorAll(selector)) {
            if (!(element instanceof HTMLElement) || !isVisible(element)) {
                continue;
            }

            const fingerprint = [
                element.tagName,
                element.id,
                element.getAttribute("role") || "",
                normalizeText(element.innerText || element.value || element.getAttribute("aria-label") || ""),
                element.getAttribute("placeholder") || "",
            ].join("|");

            if (seen.has(fingerprint)) {
                continue;
            }
            seen.add(fingerprint);

            index += 1;
            const elementId = `el_${index}`;
            scannedElements.set(elementId, element);
            elements.push({
                element_id: elementId,
                tag: element.tagName.toLowerCase(),
                role: element.getAttribute("role") || "",
                text: normalizeText(element.innerText || element.value || element.getAttribute("aria-label") || "").slice(0, 200),
                placeholder: (element.getAttribute("placeholder") || "").slice(0, 120),
                aria_label: (element.getAttribute("aria-label") || "").slice(0, 120),
                href: element.getAttribute("href") || "",
                clickable: isClickableElement(element),
                typeable: isTypeableElement(element),
            });

            if (elements.length >= limit) {
                return elements;
            }
        }
    }

    return elements;
}

function isClickableElement(element) {
    const tag = element.tagName.toLowerCase();
    return (
        tag === "button" ||
        tag === "a" ||
        element.getAttribute("role") === "button" ||
        element.getAttribute("role") === "menuitem" ||
        element.getAttribute("role") === "option" ||
        element.hasAttribute("tabindex")
    );
}

function isTypeableElement(element) {
    return isTextInput(element) || element.isContentEditable || element.getAttribute("role") === "textbox";
}

function collectSearchResults(selectors, limit) {
    const items = [];

    for (const selector of selectors) {
        const nodes = document.querySelectorAll(selector);
        for (const node of nodes) {
            const link = node.matches("a") ? node : node.querySelector("a[href]");
            const titleNode = node.querySelector("h3, .OrganicTitle-LinkText, .organic__title, .Title, .text-container") || link;
            const snippetNode = node.querySelector(".VwiC3b, .OrganicText, .text-container, .organic__content-wrapper");
            const title = normalizeText(titleNode?.innerText || link?.innerText);
            const url = link?.href || "";
            const snippet = normalizeText(snippetNode?.innerText || "");

            if (
                title &&
                url &&
                !isSearchResultGarbage(title, url, snippet) &&
                !items.some((item) => item.url === url)
            ) {
                items.push({ title, url, snippet });
            }

            if (items.length >= limit) {
                return items;
            }
        }
    }

    return items;
}

function isSearchResultGarbage(title, url, snippet) {
    const fullText = `${title} ${snippet}`.toLowerCase();
    if (!/^https?:\/\//i.test(url)) {
        return true;
    }
    if (/yandex\.ru\/an\/count/i.test(url)) {
        return true;
    }
    if (fullText.includes("может заинтересовать")) {
        return true;
    }
    if (fullText.includes("реклама")) {
        return true;
    }
    return false;
}

function collectCandidateCards(limit) {
    const selectors = [
        "[data-testid*='tile']",
        "[data-testid*='item']",
        "[data-widget*='searchResults'] [href]",
        "[class*='tile']",
        "[data-testid*='product']",
        "[class*='product']",
        "[class*='Product']",
        "article",
        "li",
        "div[data-index]",
    ];

    for (const selector of selectors) {
        const nodes = Array.from(document.querySelectorAll(selector))
            .filter((node) => {
                if (!isVisible(node)) {
                    return false;
                }
                const text = normalizeText(node.innerText);
                if (text.length <= 20) {
                    return false;
                }
                const anchors = node.matches("a[href]") ? [node] : Array.from(node.querySelectorAll("a[href]"));
                return anchors.length > 0 || /\d/.test(text);
            })
            .sort((a, b) => {
                const aScore = scoreCardCandidate(a);
                const bScore = scoreCardCandidate(b);
                return bScore - aScore;
            })
            .slice(0, limit);

        if (nodes.length) {
            return nodes;
        }
    }

    return [];
}

function extractFieldsFromCard(card, fields) {
    const titleNode = card.querySelector("h1, h2, h3, [itemprop='name'], a");
    const priceNode = card.querySelector("[class*='price'], [data-testid*='price'], [itemprop='price']");
    const linkNode = selectBestLink(card);
    const rawText = normalizeText(card.innerText);

    const item = {};
    for (const field of fields) {
        switch (field) {
            case "title":
                item.title = normalizeText(titleNode?.innerText || rawText.split("\n")[0] || rawText);
                break;
            case "price":
                item.price = parsePrice(priceNode?.innerText || rawText);
                break;
            case "currency":
                item.currency = detectCurrency(priceNode?.innerText || rawText);
                break;
            case "url":
                item.url = linkNode?.href || window.location.href;
                break;
            default:
                item[field] = normalizeText(card.querySelector(`[data-field='${field}']`)?.innerText || "");
                break;
        }
    }

    return item;
}

function parsePrice(text) {
    const match = String(text || "").replace(/\s+/g, "").match(/(\d+[.,]?\d*)/);
    return match ? match[1].replace(",", ".") : "";
}

function detectCurrency(text) {
    const normalized = String(text || "");
    if (normalized.includes("$")) {
        return "USD";
    }
    if (normalized.includes("€")) {
        return "EUR";
    }
    if (/[₽р]/i.test(normalized)) {
        return "RUB";
    }
    return "";
}

async function waitForMessageEditor(attempts, delayMs) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const editor = findMessageEditor();
        if (editor) {
            return editor;
        }

        await wait(delayMs);
    }

    return null;
}

async function waitForVkMessengerSearchInput(attempts, delayMs) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const searchInput = findVkMessengerSearchInput();
        if (searchInput) {
            return searchInput;
        }

        await wait(delayMs);
    }

    return null;
}

function findMessageEditor() {
    if (window.location.hostname.includes("vk.com")) {
        const vkEditor = findVkMessageEditor();
        if (vkEditor) {
            return vkEditor;
        }
    }

    const directMatch = findFirstVisible([
        ".im-chat-input--text",
        ".im-chat-input [contenteditable='true']",
        ".im-chat-input textarea",
        "[class*='im-chat-input'] [contenteditable='true']",
        "[class*='im-chat-input'] textarea",
        ".public-DraftEditor-content",
        "[data-testid='message_input']",
        "[data-testid='message-compose-input']",
        "[contenteditable='true'][role='textbox']",
        "div[role='textbox'][contenteditable='true']",
        "div[contenteditable='true']",
        "div[contenteditable='plaintext-only']",
        "textarea",
    ]);

    if (directMatch && !looksLikeSearchField(directMatch)) {
        return directMatch;
    }

    const candidates = Array.from(
        document.querySelectorAll("[contenteditable='true'], [role='textbox'], textarea, input[type='text']")
    ).filter((element) => isVisible(element) && !looksLikeSearchField(element));

    return candidates[0] || null;
}

function findVkMessageEditor() {
    const candidates = Array.from(
        document.querySelectorAll(
            [
                ".im-chat-input--text",
                ".im-chat-input [contenteditable='true']",
                ".im-chat-input textarea",
                "[class*='im-chat-input'] [contenteditable='true']",
                "[class*='composer'] [contenteditable='true']",
                "[class*='composer'] textarea",
                "[contenteditable='plaintext-only']",
                "[data-testid*='composer']",
            ].join(", ")
        )
    ).filter((element) => isVisible(element) && !looksLikeSearchField(element));

    if (candidates.length) {
        return candidates[0];
    }

    const containers = Array.from(document.querySelectorAll("[class*='im-chat-input'], [class*='composer'], footer"))
        .filter((element) => isVisible(element));

    for (const container of containers) {
        const nested = container.querySelector("[contenteditable='true'], [contenteditable='plaintext-only'], textarea");
        if (nested && isVisible(nested) && !looksLikeSearchField(nested)) {
            return nested;
        }
    }

    return null;
}

async function tryActivateVkSearchResult(target, searchInput, diagnostics, lookupQueries = buildVkLookupQueries(target)) {
    const beforeState = captureVkChatState();
    const resultNode = await findBestVkChatCandidateWithRetry(lookupQueries, searchInput, 7, 250);

    if (!resultNode) {
        diagnostics.push("VK-результат внутри списка диалогов не найден.");
        return { opened: false };
    }

    const candidateText = normalizeText(resultNode.innerText || resultNode.getAttribute("aria-label") || "");
    const clickable = getClickableTarget(resultNode);
    activateElement(clickable);
    diagnostics.push(`VK: выбран кандидат "${candidateText || target}" (${describeElement(clickable)}).`);

    let activationState = await waitForVkChatOpen(lookupQueries, beforeState, 2600);
    if (!activationState.opened) {
        const fallbackUrl = resolveVkChatHref(resultNode);
        if (fallbackUrl) {
            diagnostics.push(`VK fallback: открываю диалог по ссылке ${fallbackUrl}.`);
            window.location.href = fallbackUrl;
            await waitForPageToSettle();
            activationState = await waitForVkChatOpen(lookupQueries, beforeState, 2600);
        }
    }

    if (activationState.opened) {
        diagnostics.push(
            activationState.state.url !== beforeState.url
                ? `VK открыл диалог: ${beforeState.url} -> ${activationState.state.url}.`
                : "VK открыл нужный диалог без смены URL."
        );
        await waitForPageToSettle();
    } else {
        diagnostics.push("VK не подтвердил открытие нужного диалога после выбора результата.");
    }

    return {
        opened: activationState.opened,
        selectedChat: activationState.state.chatTitle || activationState.state.selectedChatText || candidateText || target,
    };
}

async function findBestVkChatCandidateWithRetry(target, searchInput, attempts, delayMs) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const candidate = findBestVkChatCandidate(target, searchInput);
        if (candidate) {
            return candidate;
        }

        await wait(delayMs);
    }

    return null;
}

function findBestVkChatCandidate(target, searchInput) {
    const scope = getVkSearchResultsScope(searchInput);
    const candidates = collectVkChatCandidates(scope)
        .map((element) => ({
            element,
            score: scoreVkChatCandidate(element, target),
        }))
        .filter((item) => item.score >= 60)
        .sort((left, right) => right.score - left.score);

    return candidates[0]?.element || null;
}

function collectVkTopCandidateDiagnostics(target, searchInput, limit) {
    const scope = getVkSearchResultsScope(searchInput);
    return collectVkChatCandidates(scope)
        .map((element) => ({
            score: scoreVkChatCandidate(element, target),
            element: describeDiagnosticElement(element),
        }))
        .sort((left, right) => right.score - left.score)
        .slice(0, limit);
}

function collectVkChatCandidates(scope) {
    const root = scope || document.body;
    const selectors = [
        "button.SearchResult",
        "[class*='SearchResult']",
        "a[href*='sel=']",
        "a[href*='/im?']",
        "[role='option']",
        "[role='menuitem']",
        "[aria-selected='true']",
        "[class*='ConvoList__item']",
        ".VirtualScrollItem",
        "[class*='conversation-item']",
        "[class*='dialog-item']",
        "[class*='Chat'] a",
        "[class*='Convo'] a",
        "[class*='Search'] [tabindex]",
        "li",
    ];
    const seen = new Set();
    const items = [];

    for (const selector of selectors) {
        for (const element of root.querySelectorAll(selector)) {
            if (!isVisible(element) || !isReasonableVkCandidate(element)) {
                continue;
            }

            const text = normalizeText(element.innerText || element.getAttribute("aria-label") || "");
            if (!text) {
                continue;
            }

            const href = element.getAttribute("href") || element.querySelector("a[href]")?.getAttribute("href") || "";
            const key = `${element.tagName}|${href}|${text.slice(0, 120)}`;
            if (seen.has(key)) {
                continue;
            }

            seen.add(key);
            items.push(element);
        }
    }

    return items;
}

function scoreVkChatCandidate(element, target) {
    const lookupQueries = coerceLookupQueries(target);
    const text = normalizeLookupText(element.innerText || element.getAttribute("aria-label") || "");
    if (!text || !lookupQueries.length) {
        return -1000;
    }

    const textTokens = tokenizeLookup(text);
    const href = element.getAttribute("href") || element.querySelector("a[href]")?.getAttribute("href") || "";
    const className = String(element.className || "").toLowerCase();
    let score = -500;

    for (const query of lookupQueries) {
        const targetText = normalizeLookupText(query);
        if (!targetText) {
            continue;
        }

        const targetTokens = tokenizeLookup(targetText);
        const matchedTokens = targetTokens.filter((token) => textTokens.includes(token) || text.includes(token));
        if (!matchedTokens.length) {
            continue;
        }

        let variantScore = matchedTokens.length * 25;
        if (matchedTokens.length === targetTokens.length) {
            variantScore += 80;
        }
        if (text === targetText) {
            variantScore += 140;
        } else if (text.startsWith(targetText)) {
            variantScore += 90;
        } else if (text.includes(targetText)) {
            variantScore += 65;
        }
        variantScore -= Math.max(0, text.length - targetText.length);
        score = Math.max(score, variantScore);
    }

    if (href.includes("sel=") || href.includes("/im?")) {
        score += 30;
    }
    if (element.tagName.toLowerCase() === "button" || element.tagName.toLowerCase() === "a") {
        score += 10;
    }
    if (element.getAttribute("role") === "option" || element.getAttribute("role") === "menuitem") {
        score += 10;
    }
    if (element.getAttribute("aria-selected") === "true" || className.includes("selected")) {
        score += 8;
    }
    if (className.includes("searchresult")) {
        score += 8;
    }
    if (text.includes("глобальный поиск") || text.includes("global search")) {
        score -= 120;
    }
    if (text === "чаты сообщения каналы") {
        score -= 150;
    }

    return score;
}

function coerceLookupQueries(target) {
    if (Array.isArray(target)) {
        return target
            .map((item) => normalizeText(item))
            .filter(Boolean);
    }

    const single = normalizeText(target);
    return single ? [single] : [];
}

function buildVkLookupQueries(target) {
    const original = normalizeText(target);
    if (!original) {
        return [];
    }

    const corrected = normalizeRussianPersonTarget(original);
    const variants = corrected && corrected.toLowerCase() !== original.toLowerCase()
        ? [corrected, original]
        : [original];

    return Array.from(new Set(variants));
}

function normalizeRussianPersonTarget(target) {
    const tokens = normalizeText(target).split(/\s+/).filter(Boolean);
    if (!tokens.length) {
        return "";
    }

    const normalizedTokens = tokens.map((token, index) =>
        normalizeRussianPersonToken(token, {
            isFirst: index === 0,
            isLast: index === tokens.length - 1,
        })
    );
    return normalizedTokens.join(" ");
}

function normalizeRussianPersonToken(token, position = {}) {
    const lower = token.toLowerCase();
    const specialMap = {
        "алексею": "Алексей",
        "андрею": "Андрей",
        "артему": "Артем",
        "артёму": "Артём",
        "василию": "Василий",
        "виктору": "Виктор",
        "евгению": "Евгений",
        "егору": "Егор",
        "ивану": "Иван",
        "игорю": "Игорь",
        "илье": "Илья",
        "максиму": "Максим",
        "матвею": "Матвей",
        "михаилу": "Михаил",
        "николаю": "Николай",
        "павлу": "Павел",
        "роману": "Роман",
        "сергею": "Сергей",
        "тимофею": "Тимофей",
        "юрию": "Юрий",
    };
    if (specialMap[lower]) {
        return matchTokenCase(token, specialMap[lower]);
    }

    const surnameRules = [
        [/ову$/i, "ов"],
        [/еву$/i, "ев"],
        [/ёву$/i, "ёв"],
        [/ину$/i, "ин"],
        [/ыну$/i, "ын"],
    ];
    for (const [pattern, replacement] of surnameRules) {
        if (pattern.test(token)) {
            return token.replace(pattern, replacement);
        }
    }

    const endingRules = [
        [/ею$/i, "ей"],
        [/ию$/i, "ий"],
        [/аю$/i, "ай"],
    ];
    for (const [pattern, replacement] of endingRules) {
        if (pattern.test(token)) {
            return token.replace(pattern, replacement);
        }
    }

    if (position.isFirst && /[бвгджзйклмнпрстфхцчшщ]у$/i.test(token) && token.length >= 4) {
        return token.slice(0, -1);
    }

    return token;
}

function matchTokenCase(source, target) {
    if (!source) {
        return target;
    }
    if (source === source.toUpperCase()) {
        return target.toUpperCase();
    }
    if (source[0] === source[0].toUpperCase()) {
        return target[0].toUpperCase() + target.slice(1);
    }
    return target.toLowerCase();
}

function normalizeLookupText(value) {
    return String(value || "")
        .toLowerCase()
        .replace(/@id\d+/g, " ")
        .replace(/[^\p{L}\p{N}]+/gu, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function tokenizeLookup(value) {
    return normalizeLookupText(value)
        .split(" ")
        .filter((token) => token.length >= 2);
}

function resolveVkChatHref(element) {
    const href = element.getAttribute("href") || element.querySelector("a[href]")?.getAttribute("href") || "";
    if (!href) {
        return "";
    }

    try {
        return new URL(href, window.location.href).toString();
    } catch (_error) {
        return "";
    }
}

function captureVkChatState() {
    const editor = findVkMessageEditor();
    return {
        url: window.location.href,
        chatTitle: findVkActiveChatTitle(),
        selectedChatText: findVkSelectedChatText(),
        editorVisible: Boolean(editor),
    };
}

async function waitForVkChatOpen(target, beforeState, timeoutMs) {
    const startedAt = Date.now();

    while (Date.now() - startedAt < timeoutMs) {
        const state = captureVkChatState();
        if (isVkChatStateMatch(state, target, beforeState)) {
            return {
                opened: true,
                state,
            };
        }

        await wait(150);
    }

    return {
        opened: false,
        state: captureVkChatState(),
    };
}

function isVkChatStateMatch(state, target, beforeState) {
    const lookupQueries = coerceLookupQueries(target);
    if (state.chatTitle && lookupQueries.some((query) => isLookupMatch(state.chatTitle, query))) {
        return true;
    }

    if (state.selectedChatText && lookupQueries.some((query) => isLookupMatch(state.selectedChatText, query))) {
        return true;
    }

    return state.editorVisible && state.url !== beforeState.url && state.url.includes("sel=");
}

function isLookupMatch(value, target) {
    const valueNormalized = normalizeLookupText(value);
    const targetNormalized = normalizeLookupText(target);
    if (!valueNormalized || !targetNormalized) {
        return false;
    }
    if (valueNormalized === targetNormalized || valueNormalized.startsWith(targetNormalized)) {
        return true;
    }

    const valueTokens = tokenizeLookup(valueNormalized);
    const targetTokens = tokenizeLookup(targetNormalized);
    return targetTokens.length > 0 && targetTokens.every((token) => valueTokens.includes(token));
}

function findVkActiveChatTitle() {
    const selectors = [
        "header h1",
        "header h2",
        "[class*='PeerName']",
        "[class*='ConversationHeader'] h1",
        "[class*='ConversationHeader'] h2",
        "[class*='chat-header'] h1",
        "[class*='chat-header'] h2",
        "[class*='Title']",
    ];

    for (const selector of selectors) {
        for (const element of document.querySelectorAll(selector)) {
            if (!isVisible(element)) {
                continue;
            }

            const text = normalizeText(element.innerText);
            if (text && text.length <= 120) {
                return text;
            }
        }
    }

    return "";
}

function findVkSelectedChatText() {
    const selectors = [
        "[aria-selected='true']",
        "[class*='selected']",
        "[class*='active']",
        ".ConvoList__item--selected",
    ];

    for (const selector of selectors) {
        const element = Array.from(document.querySelectorAll(selector)).find((candidate) => isVisible(candidate));
        const text = normalizeText(element?.innerText || "");
        if (text) {
            return text;
        }
    }

    return "";
}

function findVkSearchResult(target, searchInput) {
    const needle = target.trim().toLowerCase();
    const scope = getVkSearchResultsScope(searchInput);
    const selectors = [
        "a[href*='sel=']",
        "a[href*='/im?']",
        "[class*='Chats'] a",
        "[class*='Chat'] a",
        "[class*='Convo'] a",
        "[class*='Convo'] [role='link']",
        "[class*='Convo'] [tabindex]",
        "[class*='SearchResults'] a",
        "[class*='SearchResults'] [tabindex]",
        "[class*='Search'] a",
        "[class*='search'] a",
        "[class*='Search'] [class*='item']",
        "[class*='search'] [class*='item']",
        "[class*='Search'] [role='link']",
        "[class*='search'] [role='link']",
        "[class*='List'] [class*='item']",
        "[class*='ConvoList'] > *",
    ];

    let bestMatch = null;

    for (const selector of selectors) {
        const match = Array.from(scope.querySelectorAll(selector))
            .filter((element) => isVisible(element))
            .filter((element) => isReasonableVkCandidate(element))
            .filter((element) => {
                const text = normalizeText(element.innerText).toLowerCase();
                return text.includes(needle);
            })
            .sort((left, right) => {
                const leftScore = scoreVkCandidate(left, needle);
                const rightScore = scoreVkCandidate(right, needle);
                return leftScore - rightScore;
            })[0];

        if (match) {
            bestMatch = match;
            break;
        }
    }

    if (bestMatch) {
        return bestMatch;
    }

    return null;
}

function findVkPrimarySearchResultButton(target, searchInput) {
    const needle = target.trim().toLowerCase();
    const scope = getVkSearchResultsScope(searchInput);
    const buttons = Array.from(scope.querySelectorAll("button.SearchResult[role='menuitem'], button.SearchResult"))
        .filter((element) => isVisible(element))
        .filter((element) => {
            const text = normalizeText(element.innerText).toLowerCase();
            return text.includes(needle);
        })
        .sort((left, right) => scoreVkPrimaryResultButton(left, needle) - scoreVkPrimaryResultButton(right, needle));

    return buttons[0] || null;
}

function findVkConversationRow(target, searchInput) {
    const needle = target.trim().toLowerCase();
    const scope = getVkSearchResultsScope(searchInput);
    const selectors = [
        "[class*='Chats'] > *",
        "[class*='Chat'] > *",
        ".ConvoList__item",
        ".VirtualScrollItem",
        "[class*='ConvoList__item']",
        "[class*='SearchResult']",
        "[class*='conversation-item']",
        "[class*='dialog-item']",
        "li",
        "a[href*='sel=']",
    ];

    for (const selector of selectors) {
        const candidates = Array.from(scope.querySelectorAll(selector))
            .filter((element) => isVisible(element))
            .filter((element) => isReasonableVkCandidate(element))
            .filter((element) => {
                const text = normalizeText(element.innerText).toLowerCase();
                return text.includes(needle);
            })
            .sort((left, right) => scoreVkCandidate(left, needle) - scoreVkCandidate(right, needle));

        if (candidates.length) {
            return candidates[0];
        }
    }

    return null;
}

function findVkPopupResult(target, searchInput) {
    const needle = target.trim().toLowerCase();
    const scope = getVkMessengerScope(searchInput);
    const selectors = [
        "[role='option']",
        "[aria-selected='true']",
        "[class*='selected']",
        "[class*='active']",
        "[class*='Convo'] a[href*='sel=']",
        "[class*='Convo'] [tabindex]",
        "[class*='Dropdown'] a",
        "[class*='Popup'] a",
    ];

    for (const selector of selectors) {
        const match = Array.from(scope.querySelectorAll(selector))
            .filter((element) => isVisible(element))
            .filter((element) => isReasonableVkCandidate(element))
            .sort((left, right) => scoreVkCandidate(left, needle) - scoreVkCandidate(right, needle))
            .find((element) => {
                const text = normalizeText(element.innerText).toLowerCase();
                return !needle || text.includes(needle);
            });

        if (match) {
            return match;
        }
    }

    return null;
}

function findVkMessengerSearchInput() {
    const selectors = [
        "input[aria-label*='Поиск по чатам' i]",
        "input[aria-label*='чатам и сообщениям' i]",
        "input[placeholder='Поиск']",
        "[class*='Convo'] input[type='search']",
        "[class*='Convo'] input[placeholder*='Поиск' i]",
        "aside input[placeholder*='Поиск' i]",
    ];

    const candidates = [];
    for (const selector of selectors) {
        for (const element of document.querySelectorAll(selector)) {
            if (!isVisible(element)) {
                continue;
            }

            candidates.push(element);
        }
    }

    const scored = candidates
        .map((element) => ({
            element,
            score: scoreVkSearchInput(element),
        }))
        .sort((left, right) => left.score - right.score);

    return scored[0]?.element || null;
}

function scoreVkSearchInput(element) {
    const text = [
        element.getAttribute("placeholder"),
        element.getAttribute("aria-label"),
        element.id,
        element.className,
        element.closest("main, aside, section, [class*='Convo'], [class*='conversation'], [class*='messenger'], [class*='Search']")?.className || "",
    ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

    let score = 100;
    if (text.includes("чатам")) {
        score -= 50;
    }
    if (text.includes("сообщени")) {
        score -= 40;
    }
    if (text.includes("convo")) {
        score -= 20;
    }
    if (text.includes("music")) {
        score += 200;
    }
    if (text.includes("музык")) {
        score += 200;
    }
    if (element.closest("main")) {
        score -= 15;
    }
    if (element.closest("section")) {
        score -= 10;
    }
    if (element.closest("aside")) {
        score += 20;
    }

    return score;
}

function getVkMessengerScope(searchInput) {
    return (
        findClosestContainer(
            searchInput,
            "aside, main, section, article, div[class*='Convo'], div[class*='conversation'], div[class*='messenger']"
        ) || document.body
    );
}

function getVkSearchResultsScope(searchInput) {
    const resultsContainer = findVkResultsContainer(searchInput);
    if (resultsContainer) {
        return resultsContainer;
    }

    const context = findVkSearchContext(searchInput);
    if (context) {
        return context;
    }

    return (
        findClosestContainer(
            searchInput,
            "main, section, article, aside, div[class*='Results'], div[class*='Convo'], div[class*='conversation'], div[class*='messenger']"
        ) ||
        document.body
    );
}

function findVkChatsSection() {
    const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, div, span"))
        .filter((element) => isVisible(element))
        .find((element) => normalizeText(element.innerText).toLowerCase() === "чаты");

    if (!headings) {
        return null;
    }

    let current = headings.parentElement;
    while (current) {
        const text = normalizeText(current.innerText).toLowerCase();
        if (text.includes("чаты") && current.querySelectorAll("a, [tabindex], [role='link']").length > 0) {
            return current;
        }
        current = current.parentElement;
    }

    return headings.parentElement;
}

function findVkResultsContainer(searchInput) {
    const root =
        findVkSearchContext(searchInput) ||
        document.body;

    const selectors = [
        ".ConvoList",
        ".ConvoList__items",
        ".ConvoList__itemsWrapper",
        "[class*='ConvoList']",
        "[class*='SearchResults']",
        "[class*='search_results']",
        "[class*='ResultsList']",
        "[class*='Results']",
        "[class*='VirtualScroll']",
    ];

    for (const selector of selectors) {
        const candidates = Array.from(root.querySelectorAll(selector)).filter((element) => {
            if (!isVisible(element)) {
                return false;
            }

            const text = normalizeText(element.innerText).toLowerCase();
            if (!text) {
                return false;
            }

            if (text === "чаты сообщения каналы") {
                return false;
            }

            return element.querySelectorAll("a, [tabindex], [role='option'], .ConvoList__item, .VirtualScrollItem").length > 0;
        });

        if (candidates.length) {
            return candidates.sort((left, right) => {
                const leftScore = scoreVkResultsContainer(left);
                const rightScore = scoreVkResultsContainer(right);
                return leftScore - rightScore;
            })[0];
        }
    }

    return null;
}

function findVkSearchContext(searchInput) {
    let current = searchInput?.parentElement || null;

    while (current) {
        if (isUsefulVkSearchContext(current)) {
            return current;
        }
        current = current.parentElement;
    }

    return null;
}

function isUsefulVkSearchContext(element) {
    if (!(element instanceof HTMLElement) || !isVisible(element)) {
        return false;
    }

    const className = String(element.className || "");
    const text = normalizeText(element.innerText);
    const candidateCount = element.querySelectorAll("a, [tabindex], [role='option'], .ConvoList__item, .VirtualScrollItem").length;

    if (className.includes("vkuiSearch__input") || className.includes("vkuiSearch")) {
        return false;
    }

    if (text.length < 40) {
        return false;
    }

    if (candidateCount < 3) {
        return false;
    }

    return true;
}

function scoreVkResultsContainer(element) {
    const className = String(element.className || "");
    const text = normalizeText(element.innerText).toLowerCase();
    let score = 100;

    if (className.includes("ConvoList")) {
        score -= 40;
    }
    if (className.includes("VirtualScroll")) {
        score -= 20;
    }
    if (className.includes("Results")) {
        score -= 15;
    }
    if (text.includes("глобальный поиск")) {
        score += 15;
    }
    if (text === "чаты сообщения каналы") {
        score += 500;
    }

    return score;
}

function findClosestContainer(element, selector) {
    let current = element?.parentElement || null;

    while (current) {
        if (current.matches(selector)) {
            return current;
        }
        current = current.parentElement;
    }

    return null;
}

function findFirstVisible(selectors) {
    for (const selector of selectors) {
        const elements = document.querySelectorAll(selector);
        for (const element of elements) {
            if (isVisible(element)) {
                return element;
            }
        }
    }

    return null;
}

function scoreVkCandidate(element, needle) {
    const text = normalizeText(element.innerText).toLowerCase();
    const href = element.getAttribute("href") || "";
    const isAnchor = element.tagName.toLowerCase() === "a" ? 0 : 20;
    const hasSel = href.includes("sel=") ? -20 : 0;
    const exactText = text === needle ? -15 : 0;
    const textPenalty = Math.min(text.length, 200);
    const childPenalty = Math.min(element.querySelectorAll("*").length, 200);
    return isAnchor + hasSel + exactText + textPenalty + childPenalty;
}

function scoreVkPrimaryResultButton(element, needle) {
    const text = normalizeText(element.innerText).toLowerCase();
    const className = String(element.className || "");
    let score = 100;

    if (element.tagName.toLowerCase() === "button") {
        score -= 30;
    }
    if (element.getAttribute("role") === "menuitem") {
        score -= 20;
    }
    if (className.includes("SearchResult--selected")) {
        score -= 40;
    }
    if (text === needle) {
        score -= 25;
    }
    if (text.startsWith(needle)) {
        score -= 15;
    }
    if (text.includes("заходил")) {
        score -= 10;
    }
    if (text.includes("@id")) {
        score += 10;
    }
    if (text.includes("глобальный поиск")) {
        score += 50;
    }

    score += Math.min(text.length, 200);
    return score;
}

function isReasonableVkCandidate(element) {
    const text = normalizeText(element.innerText);
    const childCount = element.querySelectorAll("*").length;
    const className = String(element.className || "");

    if (text.length > 220 || childCount > 45) {
        return false;
    }

    if (className.includes("itemsWrapper") || className.includes("ConvoList__items")) {
        return false;
    }

    return true;
}

function findElementByText(text) {
    const needle = text.trim().toLowerCase();
    const candidates = Array.from(document.querySelectorAll("a, button, div, span"));

    return (
        candidates.find((element) => {
            if (!isVisible(element)) {
                return false;
            }

            const content = normalizeText(element.innerText).toLowerCase();
            return content === needle || content.includes(needle);
        }) || null
    );
}

function getClickableTarget(element) {
    if (window.location.hostname.includes("vk.com")) {
        const preciseVkTarget = element.matches?.("button.SearchResult, .SearchResult, a[href*='sel='], a[href*='/im?'], [role='menuitem'], [role='option'], [tabindex], .ConvoList__item, .VirtualScrollItem")
            ? element
            : element.querySelector?.("button.SearchResult, .SearchResult, a[href*='sel='], a[href*='/im?'], [role='menuitem'], [role='option'], [tabindex], .ConvoList__item, .VirtualScrollItem");
        if (preciseVkTarget && isVisible(preciseVkTarget)) {
            return preciseVkTarget;
        }
    }

    let current = element;
    while (current) {
        if (isGoodClickableTarget(current)) {
            return current;
        }

        current = current.parentElement;
    }

    return element;
}

function isGoodClickableTarget(element) {
    if (!(element instanceof HTMLElement) || !isVisible(element)) {
        return false;
    }

    const tag = element.tagName.toLowerCase();
    const className = String(element.className || "");
    const text = normalizeText(element.innerText);
    const childCount = element.querySelectorAll("*").length;

    if (tag === "a" || tag === "button") {
        return true;
    }

    if (element.getAttribute("role") === "button") {
        return true;
    }

    if (element.hasAttribute("tabindex") && childCount < 40 && text.length < 180) {
        return true;
    }

    if ((className.includes("item") || className.includes("row")) && childCount < 40 && text.length < 180) {
        return true;
    }

    return false;
}

function findButtonByText(labels, options = {}) {
    const exact = options.exact !== false;
    const lowerLabels = labels.map((label) => label.toLowerCase());
    const buttons = Array.from(document.querySelectorAll("button, div[role='button'], span[role='button']"));

    return (
        buttons.find((button) => {
            if (!isVisible(button)) {
                return false;
            }

            const content = normalizeText(
                [
                    button.innerText,
                    button.getAttribute("aria-label"),
                    button.getAttribute("title"),
                ].filter(Boolean).join(" ")
            ).toLowerCase();
            return exact
                ? lowerLabels.includes(content)
                : lowerLabels.some((label) => content === label || content.includes(label));
        }) || null
    );
}

function scoreCardCandidate(card) {
    const text = normalizeText(card.innerText);
    const anchors = card.matches("a[href]") ? [card] : Array.from(card.querySelectorAll("a[href]"));
    const price = parseFloat(parsePrice(text) || "0");
    let score = Math.min(text.length, 200);
    if (anchors.length) {
        score += 50;
    }
    if (price > 0) {
        score += 80;
    }
    if (/iphone|samsung|xiaomi|apple/i.test(text)) {
        score += 40;
    }
    return score;
}

function selectBestLink(card) {
    const currentUrl = new URL(window.location.href);
    const anchors = (card.matches("a[href]") ? [card] : Array.from(card.querySelectorAll("a[href]")))
        .filter((anchor) => isVisible(anchor))
        .map((anchor) => ({
            anchor,
            href: anchor.href || "",
            text: normalizeText(anchor.innerText || anchor.getAttribute("aria-label") || ""),
        }))
        .filter((item) => /^https?:\/\//i.test(item.href));

    if (!anchors.length) {
        return null;
    }

    anchors.sort((left, right) => scoreLinkCandidate(right, currentUrl) - scoreLinkCandidate(left, currentUrl));
    return anchors[0].anchor;
}

function scoreLinkCandidate(item, currentUrl) {
    let score = 0;
    try {
        const candidate = new URL(item.href);
        if (candidate.origin === currentUrl.origin) {
            score += 20;
        }
        if (candidate.pathname !== currentUrl.pathname) {
            score += 40;
        }
        if (/\/product\/|\/item\/|\/goods\/|\/catalog\/|\/sale\/|\/offer\//i.test(candidate.pathname)) {
            score += 120;
        }
        if (/\/category\/|\/search\/|\/brand\/|\/cars\/[^/]+\/[^/]+\/\d+\/(used|all|new)(\/do-\d+)?\/?$/i.test(candidate.pathname)) {
            score -= 80;
        }
        if (candidate.search && !/sku|product|item/i.test(candidate.search)) {
            score -= 10;
        }
        if (candidate.pathname.split("/").length > currentUrl.pathname.split("/").length) {
            score += 15;
        }
    } catch (_error) {
        score -= 100;
    }

    score += Math.min(item.text.length, 80);
    if (/iphone|samsung|xiaomi|apple/i.test(item.text)) {
        score += 30;
    }
    return score;
}

function findLinkByText(labels) {
    const lowerLabels = labels.map((label) => label.toLowerCase());
    const links = Array.from(document.querySelectorAll("a[href]"));

    return (
        links.find((link) => {
            if (!isVisible(link)) {
                return false;
            }

            const content = normalizeText(link.innerText).toLowerCase();
            return lowerLabels.includes(content);
        }) || null
    );
}

function focusAndReplace(element, value) {
    element.focus();

    if (isTextInput(element)) {
        const prototype = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
        if (descriptor?.set) {
            descriptor.set.call(element, value);
        } else {
            element.value = value;
        }
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return;
    }

    if (element.isContentEditable) {
        replaceContentEditableValue(element, value);
    }
}

function replaceContentEditableValue(element, value) {
    const selection = window.getSelection();
    const safeValue = String(value || "");

    try {
        selection?.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(element);
        selection?.addRange(range);

        element.dispatchEvent(
            new InputEvent("beforeinput", {
                bubbles: true,
                cancelable: true,
                data: null,
                inputType: "deleteContentBackward",
            })
        );

        range.deleteContents();
        element.textContent = "";

        element.dispatchEvent(
            new InputEvent("beforeinput", {
                bubbles: true,
                cancelable: true,
                data: safeValue,
                inputType: "insertText",
            })
        );

        let inserted = false;
        if (typeof document.execCommand === "function") {
            try {
                inserted = document.execCommand("insertText", false, safeValue);
            } catch (_error) {
                inserted = false;
            }
        }

        if (!inserted) {
            const textNode = document.createTextNode(safeValue);
            range.deleteContents();
            range.insertNode(textNode);
            range.setStartAfter(textNode);
            range.collapse(true);
            selection?.removeAllRanges();
            selection?.addRange(range);
        }

        element.dispatchEvent(
            new InputEvent("input", {
                bubbles: true,
                data: safeValue,
                inputType: "insertText",
            })
        );
        element.dispatchEvent(new Event("change", { bubbles: true }));
        dispatchKey(element, "End");
        dispatchKey(element, "ArrowRight");
        dispatchKey(element, " ", { code: "Space" });
        dispatchKey(element, "Backspace");
    } catch (_error) {
        element.textContent = safeValue;
        element.dispatchEvent(new InputEvent("input", { bubbles: true, data: safeValue, inputType: "insertText" }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
    }
}

function dispatchEnter(element) {
    dispatchKey(element, "Enter");
}

function dispatchKey(element, key, options = {}) {
    const code = options.code || (key === " " ? "Space" : key);
    const keyCodeMap = {
        Enter: 13,
        " ": 32,
        Backspace: 8,
        End: 35,
        ArrowRight: 39,
    };
    const keyCode = keyCodeMap[key];

    for (const eventName of ["keydown", "keypress", "keyup"]) {
        element.dispatchEvent(
            new KeyboardEvent(eventName, {
                key,
                code,
                bubbles: true,
                cancelable: true,
                ctrlKey: Boolean(options.ctrlKey),
                shiftKey: Boolean(options.shiftKey),
                altKey: Boolean(options.altKey),
                metaKey: Boolean(options.metaKey),
                keyCode,
                which: keyCode,
            })
        );
    }
}

function activateElement(element) {
    element.scrollIntoView?.({ block: "center", inline: "center" });
    element.focus?.();

    try {
        element.click?.();
    } catch (_error) {
        // Fall through to synthetic events below.
    }

    for (const eventName of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
        element.dispatchEvent(
            new MouseEvent(eventName, {
                bubbles: true,
                cancelable: true,
                view: window,
            })
        );
    }

    if (element.matches?.("button, [role='button'], [role='menuitem'], [tabindex]")) {
        for (const key of ["Enter", " "]) {
            element.dispatchEvent(
                new KeyboardEvent("keydown", {
                    key,
                    code: key === " " ? "Space" : key,
                    bubbles: true,
                    cancelable: true,
                })
            );
            element.dispatchEvent(
                new KeyboardEvent("keyup", {
                    key,
                    code: key === " " ? "Space" : key,
                    bubbles: true,
                    cancelable: true,
                })
            );
        }
    }
}

function looksLikeSearchField(element) {
    const text = [
        element.getAttribute("placeholder"),
        element.getAttribute("aria-label"),
        element.getAttribute("name"),
        element.id,
        element.className,
    ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

    return text.includes("search") || text.includes("поиск") || text.includes("finder");
}

function isTextInput(element) {
    return element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement;
}

function isVisible(element) {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
}

function normalizeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function describeElement(element) {
    const parts = [element.tagName.toLowerCase()];
    if (element.id) {
        parts.push(`#${element.id}`);
    }
    if (typeof element.className === "string" && element.className.trim()) {
        parts.push(`.${element.className.trim().replace(/\s+/g, ".")}`);
    }
    return parts.join("");
}

function collectElementDiagnostics(selector, limit) {
    return Array.from(document.querySelectorAll(selector))
        .filter((element) => isVisible(element))
        .slice(0, limit)
        .map((element) => ({
            tag: element.tagName.toLowerCase(),
            text: normalizeText(element.innerText || element.value || element.getAttribute("aria-label")),
            placeholder: element.getAttribute("placeholder") || "",
            ariaLabel: element.getAttribute("aria-label") || "",
            role: element.getAttribute("role") || "",
            id: element.id || "",
            classes: String(element.className || ""),
        }));
}

function collectVkResultCandidates(scope) {
    const root = scope || document.body;
    const selectors = [
        "a",
        "[role='link']",
        "[role='option']",
        "[tabindex]",
        ".ConvoList__item",
        ".VirtualScrollItem",
        "[class*='Convo']",
        "[class*='Chat']",
        "[class*='SearchResult']",
        "li",
    ];

    const seen = new Set();
    const items = [];

    for (const selector of selectors) {
        for (const element of root.querySelectorAll(selector)) {
            if (!isVisible(element)) {
                continue;
            }

            const text = normalizeText(element.innerText);
            if (!text) {
                continue;
            }

            const key = `${element.tagName}:${element.className}:${text.slice(0, 80)}`;
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);

            items.push(describeDiagnosticElement(element));
            if (items.length >= 40) {
                return items;
            }
        }
    }

    return items;
}

function describeDiagnosticElement(element) {
    return {
        tag: element.tagName.toLowerCase(),
        text: normalizeText(element.innerText || element.value || element.getAttribute("aria-label")),
        href: element.getAttribute("href") || "",
        role: element.getAttribute("role") || "",
        tabindex: element.getAttribute("tabindex") || "",
        placeholder: element.getAttribute("placeholder") || "",
        ariaLabel: element.getAttribute("aria-label") || "",
        id: element.id || "",
        classes: String(element.className || ""),
    };
}

function capturePageState() {
    return {
        url: window.location.href,
        title: document.title,
        bodyTextLength: normalizeText(document.body?.innerText || "").length,
        visibleLinks: Array.from(document.querySelectorAll("a"))
            .filter((element) => isVisible(element))
            .slice(0, 20).length,
    };
}

async function waitForNavigationOrContentChange(beforeState, timeoutMs) {
    const startedAt = Date.now();

    while (Date.now() - startedAt < timeoutMs) {
        const currentState = capturePageState();
        const urlChanged = currentState.url !== beforeState.url;
        const titleChanged = currentState.title !== beforeState.title;
        const bodyShift = Math.abs(currentState.bodyTextLength - beforeState.bodyTextLength) > 120;
        const linksShift = Math.abs(currentState.visibleLinks - beforeState.visibleLinks) > 3;

        if (urlChanged || titleChanged || bodyShift || linksShift) {
            return {
                changed: true,
                urlChanged,
                fromUrl: beforeState.url,
                toUrl: currentState.url,
            };
        }

        await wait(150);
    }

    return {
        changed: false,
        urlChanged: false,
        fromUrl: beforeState.url,
        toUrl: beforeState.url,
    };
}

async function waitForPageToSettle() {
    if (document.readyState !== "complete") {
        await new Promise((resolve) => {
            const onReady = () => {
                if (document.readyState === "complete") {
                    document.removeEventListener("readystatechange", onReady);
                    resolve();
                }
            };

            document.addEventListener("readystatechange", onReady);
            setTimeout(() => {
                document.removeEventListener("readystatechange", onReady);
                resolve();
            }, 2000);
        });
    }

    await wait(800);
}

function buildErrorEnvelope(envelope, code, message, retryable, details = {}, durationMs = 0) {
    return {
        trace_id: envelope?.trace_id || crypto.randomUUID(),
        session_id: envelope?.session_id || getSessionId(),
        tool: envelope?.tool || "unknown",
        ok: false,
        output: details,
        error: {
            code,
            message,
            retryable,
        },
        duration_ms: durationMs,
    };
}

function createToolError(code, message, retryable, details = {}) {
    const error = new Error(message);
    error.code = code;
    error.retryable = retryable;
    error.details = details;
    return error;
}

function getSessionId() {
    return window.sessionStorage.aiHelperSessionId || (window.sessionStorage.aiHelperSessionId = crypto.randomUUID());
}
