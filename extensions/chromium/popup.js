document.getElementById("sendTest").onclick = () => {
    chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
        chrome.runtime.sendMessage({
            action: "runCommand",
            command: { type: "send_vk_message", user: "Иван", text: "привет" }
        });
    });
};