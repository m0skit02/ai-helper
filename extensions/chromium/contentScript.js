const AI_HELPER_CONTENT_MESSAGE = "AI_HELPER_CONTENT_REQUEST";
const MAX_RESULTS = 10;

const draftedActions = new Map();

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

async function handleBrowserMessageDraft(input) {
    const target = String(input?.destination_hint || "").trim();
    const messageText = String(input?.message_text || "").trim();

    if (!target) {
        throw createToolError("INTERNAL", "Не передан destination_hint.", false);
    }

    if (!messageText) {
        throw createToolError("INTERNAL", "Не передан message_text.", false);
    }

    const diagnostics = [];
    const searchInput = window.location.hostname.includes("vk.com")
        ? findVkMessengerSearchInput()
        : findFirstVisible([
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

    if (window.location.hostname.includes("vk.com") && searchInput) {
        const vkActivation = await tryActivateVkSearchResult(target, searchInput, diagnostics);
        if (vkActivation?.opened) {
            const editorAfterVkActivation = await waitForMessageEditor(10, 350);
            if (editorAfterVkActivation) {
                focusAndReplace(editorAfterVkActivation, messageText);
                diagnostics.push(`Сообщение подготовлено в ${describeElement(editorAfterVkActivation)}.`);

                const actionId = crypto.randomUUID();
                draftedActions.set(actionId, {
                    target,
                    messageText,
                    createdAt: Date.now(),
                    editorDescriptor: describeElement(editorAfterVkActivation),
                });

                return {
                    draft_ready: true,
                    action_id: actionId,
                    preview: {
                        target,
                        message_text: messageText,
                    },
                    diagnostics,
                };
            }
        }
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

    const actionId = crypto.randomUUID();
    draftedActions.set(actionId, {
        target,
        messageText,
        createdAt: Date.now(),
        editorDescriptor: describeElement(editor),
    });

    return {
        draft_ready: true,
        action_id: actionId,
        preview: {
            target,
            message_text: messageText,
        },
        diagnostics,
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

    const sendButton = findButtonByText(["Отправить", "Send", "Отослать"]);
    if (sendButton) {
        sendButton.click();
    } else {
        dispatchEnter(editor);
    }

    draftedActions.delete(actionId);

    return {
        action_id: actionId,
        status: "sent",
        preview: {
            target: draft.target,
            message_text: draft.messageText,
        },
    };
}

function inspectPage() {
    return {
        url: window.location.href,
        title: document.title,
        inputs: collectElementDiagnostics("input, textarea, [contenteditable='true'], [role='textbox']", 10),
        buttons: collectElementDiagnostics("button, [role='button']", 12),
        links: collectElementDiagnostics("a", 10),
        lists: collectElementDiagnostics("tr, li, article, div[role='listitem'], .message", 10),
    };
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

            if (title && url && !items.some((item) => item.url === url)) {
                items.push({ title, url, snippet });
            }

            if (items.length >= limit) {
                return items;
            }
        }
    }

    return items;
}

function collectCandidateCards(limit) {
    const selectors = [
        "[data-testid*='product']",
        "[class*='product']",
        "[class*='Product']",
        "article",
        "li",
        "div[data-index]",
    ];

    for (const selector of selectors) {
        const nodes = Array.from(document.querySelectorAll(selector))
            .filter((node) => isVisible(node) && normalizeText(node.innerText).length > 20)
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
    const linkNode = card.querySelector("a[href]");
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

async function tryActivateVkSearchResult(target, searchInput, diagnostics) {
    const beforeState = capturePageState();
    const resultNode =
        findVkPrimarySearchResultButton(target, searchInput) ||
        findVkSearchResult(target, searchInput) ||
        findVkConversationRow(target, searchInput);

    if (resultNode) {
        const clickable = getClickableTarget(resultNode);
        activateElement(clickable);
        diagnostics.push(`VK-результат поиска найден и активирован (${describeElement(clickable)}).`);
    } else {
        diagnostics.push("VK-результат внутри списка диалогов не найден.");
        return { opened: false };
    }

    let navigationState = await waitForNavigationOrContentChange(beforeState, 1800);
    if (!navigationState.changed) {
        const popupResult = findVkPopupResult(target, searchInput);
        if (popupResult) {
            const popupClickable = getClickableTarget(popupResult);
            activateElement(popupClickable);
            diagnostics.push(`VK показал popover с результатами, активирую конкретный пункт (${describeElement(popupClickable)}).`);
            navigationState = await waitForNavigationOrContentChange(beforeState, 2200);
        } else {
            diagnostics.push("VK не открыл диалог после первичной активации.");
        }
    }

    if (navigationState.changed) {
        diagnostics.push(
            navigationState.urlChanged
                ? `VK после выбора результата изменил URL: ${navigationState.fromUrl} -> ${navigationState.toUrl}.`
                : "VK после выбора результата обновил DOM."
        );
        await waitForPageToSettle();
    } else {
        diagnostics.push("VK после активации результата не показал явной навигации.");
    }

    return {
        opened: navigationState.changed,
    };
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
        const preciseVkTarget = element.matches?.("a[href*='sel='], a[href*='/im?'], [role='option'], [tabindex], .ConvoList__item, .VirtualScrollItem")
            ? element
            : element.querySelector?.("a[href*='sel='], a[href*='/im?'], [role='option'], [tabindex], .ConvoList__item, .VirtualScrollItem");
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

function findButtonByText(labels) {
    const lowerLabels = labels.map((label) => label.toLowerCase());
    const buttons = Array.from(document.querySelectorAll("button, div[role='button'], span[role='button']"));

    return (
        buttons.find((button) => {
            if (!isVisible(button)) {
                return false;
            }

            const content = normalizeText(button.innerText).toLowerCase();
            return lowerLabels.includes(content);
        }) || null
    );
}

function focusAndReplace(element, value) {
    element.focus();

    if (isTextInput(element)) {
        element.value = value;
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return;
    }

    if (element.isContentEditable) {
        element.textContent = value;
        element.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
    }
}

function dispatchEnter(element) {
    dispatchKey(element, "Enter");
}

function dispatchKey(element, key) {
    element.dispatchEvent(
        new KeyboardEvent("keydown", {
            key,
            code: key,
            bubbles: true,
        })
    );
    element.dispatchEvent(
        new KeyboardEvent("keyup", {
            key,
            code: key,
            bubbles: true,
        })
    );
}

function activateElement(element) {
    element.focus?.();

    for (const eventName of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
        element.dispatchEvent(
            new MouseEvent(eventName, {
                bubbles: true,
                cancelable: true,
                view: window,
            })
        );
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
