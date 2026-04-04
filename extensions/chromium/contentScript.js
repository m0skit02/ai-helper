window.runAICommand = (command) => {
    if (command.type === "send_vk_message") {
        const input = document.querySelector('textarea'); // пример для VK
        if (input) {
            input.value = command.text;
            const event = new Event('input', { bubbles: true });
            input.dispatchEvent(event);

            const sendBtn = document.querySelector('button.send'); // пример
            if (sendBtn) sendBtn.click();
        }
    }
};