chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "runCommand") {
        chrome.scripting.executeScript({
            target: { tabId: sender.tab.id },
            func: (command) => {
                // вызываем функцию в contentScript
                window.runAICommand(command);
            },
            args: [msg.command]
        });
    }
});