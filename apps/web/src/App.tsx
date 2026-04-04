import { useState, useRef, useEffect } from "react";

type Message = {
    role: "user" | "assistant";
    text: string;
};

export default function ChatPage() {
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState("");
    const chatEndRef = useRef<HTMLDivElement | null>(null);

    const sendMessage = () => {
        if (!input.trim()) return;

        setMessages([...messages, { role: "user", text: input }]);
        setInput("");

        // mock response
        setTimeout(() => {
            setMessages((prev) => [
                ...prev,
                { role: "assistant", text: "Обработка запроса..." },
            ]);
        }, 500);
    };

    useEffect(() => {
        chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    return (
        <div className="flex flex-col h-screen bg-gray-100">
            {/* Header */}
            <div className="p-4 bg-white shadow">
                <h1 className="text-xl font-semibold">AI Assistant</h1>
            </div>

            {/* Chat */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {messages.map((msg, index) => (
                    <div
                        key={index}
                        className={`flex ${
                            msg.role === "user" ? "justify-end" : "justify-start"
                        }`}
                    >
                        <div
                            className={`max-w-xs px-4 py-2 rounded-2xl shadow ${
                                msg.role === "user" ? "bg-blue-500 text-white" : "bg-white text-black"
                            }`}
                        >
                            {msg.text}
                        </div>
                    </div>
                ))}
                <div ref={chatEndRef} />
            </div>

            {/* Fixed Input at Bottom */}
            <div className="fixed bottom-0 left-0 w-full p-4 bg-white border-t flex gap-2">
                <input
                    className="flex-1 border rounded-2xl px-4 py-2 outline-none"
                    placeholder="Введите запрос..."
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                />
                <button
                    onClick={sendMessage}
                    className="bg-blue-500 text-white px-4 py-2 rounded-2xl"
                >
                    Отправить
                </button>
            </div>
        </div>
    );
}