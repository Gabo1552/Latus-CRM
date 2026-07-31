import { useState, useEffect, useRef, useCallback } from "react";
import { useParams } from "react-router-dom";
import { Send, CheckCheck, Lock, PhoneOff } from "lucide-react";
import axios from "axios";

const rawUrl = (process.env.REACT_APP_BACKEND_URL || "http://localhost:8000").replace(/\/$/, "");
const API_URL = rawUrl.endsWith("/api") ? rawUrl : `${rawUrl}/api`;

const publicApi = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

function TypingIndicator({ color }) {
  return (
    <div className="flex justify-start items-end gap-2 px-1">
      <div
        className="h-8 w-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs font-bold shadow-sm"
        style={{ background: color }}
      >
        IA
      </div>
      <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "0ms" }} />
          <span className="h-2 w-2 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "150ms" }} />
          <span className="h-2 w-2 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "300ms" }} />
        </div>
      </div>
    </div>
  );
}

function ChatMessage({ msg, session, isLatest }) {
  const primaryColor = session?.webchat_primary_color || "#0E8DDB";
  const userBubble = session?.webchat_user_bubble_color || primaryColor;
  const avatarUrl = session?.webchat_avatar_url;
  const botName = session?.bot_name || "Bot";

  const isContact = msg.sender_type === "contact";
  const isBot = msg.sender_type === "bot";
  const isSystem = msg.sender_type === "system";

  const timeStr = msg.created_at
    ? new Date(msg.created_at).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" })
    : "";

  if (isSystem) {
    return (
      <div className="flex justify-center my-1">
        <span className="text-[11px] font-medium text-slate-400 bg-white/70 border border-slate-200 px-3 py-1 rounded-full">
          {msg.body}
        </span>
      </div>
    );
  }

  return (
    <div
      className={`flex items-end gap-2 ${isContact ? "flex-row-reverse" : "flex-row"} px-1`}
      style={{ animation: isLatest ? "slideIn 0.22s ease-out both" : undefined }}
    >
      {!isContact && (
        <div className="flex-shrink-0 h-8 w-8 rounded-full overflow-hidden shadow-sm" style={{ background: primaryColor }}>
          {avatarUrl
            ? <img src={avatarUrl} alt={botName} className="h-full w-full object-cover" />
            : <div className="h-full w-full flex items-center justify-center text-white text-xs font-bold">{botName.charAt(0).toUpperCase()}</div>
          }
        </div>
      )}
      <div className={`flex flex-col max-w-[78%] ${isContact ? "items-end" : "items-start"}`}>
        {isBot && (
          <span className="text-[10px] font-semibold text-slate-500 mb-0.5 px-1">{botName}</span>
        )}
        <div
          className={`px-3.5 py-2.5 shadow-sm text-sm leading-relaxed whitespace-pre-wrap break-words ${
            isContact
              ? "text-white rounded-2xl rounded-br-sm"
              : "text-slate-800 bg-white border border-slate-200 rounded-2xl rounded-bl-sm"
          }`}
          style={isContact ? { backgroundColor: userBubble } : {}}
        >
          {msg.body}
          <div className={`flex items-center justify-end gap-1 mt-1 text-[10px] ${isContact ? "text-white/70" : "text-slate-400"}`}>
            <span>{timeStr}</span>
            {isContact && <CheckCheck className="h-3 w-3" />}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function PublicWebChat() {
  const { token } = useParams();
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [finished, setFinished] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [botTyping, setBotTyping] = useState(false);

  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const prevMsgCount = useRef(0);

  const scrollToBottom = useCallback((behavior = "smooth") => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior });
    }
  }, []);

  useEffect(() => {
    let active = true;
    const init = async () => {
      try {
        const res = await publicApi.post("/public/webchat/session", { session_token: token });
        if (active) {
          setSession(res.data);
          setMessages(res.data.messages || []);
          setLoading(false);
        }
      } catch {
        if (active) { setError(true); setLoading(false); }
      }
    };
    init();
    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    if (!token || loading) return;
    const interval = setInterval(async () => {
      try {
        const res = await publicApi.get(`/public/webchat/${token}/messages`);
        if (res.data?.messages) {
          const newMsgs = res.data.messages;
          setMessages(prev => {
            if (newMsgs.length > prev.length) setBotTyping(false);
            return newMsgs;
          });
        }
      } catch { /* silent */ }
    }, 2500);
    return () => clearInterval(interval);
  }, [token, loading]);

  useEffect(() => {
    if (messages.length > prevMsgCount.current) scrollToBottom();
    prevMsgCount.current = messages.length;
  }, [messages.length, scrollToBottom]);

  const handleDraftChange = (e) => {
    setDraft(e.target.value);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + "px";
    }
  };

  const handleSend = async () => {
    if (!draft.trim() || sending || finished) return;
    const text = draft.trim();
    setDraft("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setSending(true);
    setBotTyping(true);
    const tempMsg = {
      id: `temp_${Date.now()}`,
      sender_type: "contact",
      sender_name: session?.contact_name || "Tu",
      body: text,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, tempMsg]);
    scrollToBottom();
    try {
      const res = await publicApi.post(`/public/webchat/${token}/messages`, {
        body: text,
        sender_name: session?.contact_name || "Visitante Web",
      });
      if (res.data?.messages) {
        setMessages(res.data.messages);
        setBotTyping(false);
      }
    } catch {
      setBotTyping(false);
    } finally {
      setSending(false);
    }
  };

  const handleFinish = async () => {
    if (finishing || finished) return;
    setFinishing(true);
    try {
      await publicApi.post(`/public/webchat/${token}/finish`);
      setFinished(true);
    } catch { /* silent */ }
    finally { setFinishing(false); }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "#EFF2F5" }}>
        <div className="flex flex-col items-center gap-3">
          <div className="h-12 w-12 rounded-full border-4 border-[#0E8DDB] border-t-transparent animate-spin" />
          <p className="text-sm font-medium text-slate-500">Conectando...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4" style={{ background: "#EFF2F5" }}>
        <div className="bg-white rounded-2xl p-8 max-w-sm w-full text-center shadow-lg border border-slate-200">
          <div className="h-16 w-16 rounded-full bg-slate-100 flex items-center justify-center mx-auto mb-4">
            <PhoneOff className="h-8 w-8 text-slate-400" />
          </div>
          <h2 className="text-lg font-bold text-slate-800">Sesion no encontrada</h2>
          <p className="text-sm text-slate-500 mt-1.5">Este enlace puede haber expirado o ser invalido.</p>
        </div>
      </div>
    );
  }

  const primaryColor = session?.webchat_primary_color || "#0E8DDB";
  const bgColor = session?.webchat_bg_color || "#EFF2F5";
  const title = session?.webchat_title || "Asistente";
  const welcomeMsg = session?.webchat_welcome_message || "Hola! En que puedo ayudarte hoy?";
  const avatarUrl = session?.webchat_avatar_url;
  const botName = session?.bot_name || "Bot";

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        @keyframes slideIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.12); border-radius: 4px; }
      `}</style>
      <div
        className="min-h-screen flex flex-col items-center justify-center sm:p-4"
        style={{ background: bgColor, fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif" }}
      >
        <div
          className="w-full max-w-[460px] h-screen sm:h-[92vh] sm:max-h-[780px] flex flex-col sm:rounded-2xl overflow-hidden shadow-2xl"
          style={{ background: "#ffffff" }}
        >
          <div className="flex items-center gap-3 px-4 py-3 shrink-0" style={{ background: primaryColor }}>
            <div className="h-10 w-10 rounded-full overflow-hidden flex-shrink-0 shadow border-2 border-white/30"
              style={{ background: "rgba(255,255,255,0.25)" }}>
              {avatarUrl
                ? <img src={avatarUrl} alt={botName} className="h-full w-full object-cover" />
                : <div className="h-full w-full flex items-center justify-center text-white font-bold text-base">{botName.charAt(0).toUpperCase()}</div>
              }
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-white font-semibold text-[15px] leading-tight truncate">{title}</h1>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse flex-shrink-0" />
                <span className="text-white/85 text-[11px] font-medium">En linea - Respuesta inmediata</span>
              </div>
            </div>
            <button
              type="button"
              onClick={handleFinish}
              disabled={finishing || finished}
              className="text-[11px] font-semibold text-white/90 hover:text-white bg-white/15 hover:bg-white/25 px-3 py-1.5 rounded-lg transition-all flex-shrink-0 disabled:opacity-60"
            >
              {finished ? "Listo" : finishing ? "..." : "Finalizar"}
            </button>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto py-4 px-3 space-y-2.5" style={{ background: bgColor }}>
            <div className="flex items-end gap-2 px-1" style={{ animation: "slideIn 0.3s ease-out" }}>
              <div className="flex-shrink-0 h-8 w-8 rounded-full overflow-hidden shadow-sm" style={{ background: primaryColor }}>
                {avatarUrl
                  ? <img src={avatarUrl} alt={botName} className="h-full w-full object-cover" />
                  : <div className="h-full w-full flex items-center justify-center text-white text-xs font-bold">{botName.charAt(0).toUpperCase()}</div>
                }
              </div>
              <div className="flex flex-col items-start max-w-[78%]">
                <span className="text-[10px] font-semibold text-slate-500 mb-0.5 px-1">{botName}</span>
                <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-3.5 py-2.5 shadow-sm">
                  <p className="text-sm text-slate-800 leading-relaxed">{welcomeMsg}</p>
                </div>
              </div>
            </div>

            {finished && (
              <div className="flex justify-center">
                <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-xl px-4 py-2.5 text-[12px] font-medium text-center max-w-xs">
                  Consulta finalizada. Enviamos el resumen a tu WhatsApp.
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <ChatMessage key={m.id} msg={m} session={session} isLatest={i === messages.length - 1} />
            ))}

            {botTyping && <TypingIndicator color={primaryColor} />}
          </div>

          <div className="bg-white border-t border-slate-200 px-3 py-3 shrink-0">
            <div className="flex items-end gap-2">
              <div className="flex-1 bg-slate-50 border border-slate-200 rounded-2xl px-3.5 py-2 focus-within:border-slate-400 focus-within:bg-white transition-all">
                <textarea
                  ref={textareaRef}
                  rows={1}
                  value={draft}
                  onChange={handleDraftChange}
                  onKeyDown={handleKeyDown}
                  disabled={finished}
                  placeholder={finished ? "Consulta finalizada" : "Escribi tu mensaje..."}
                  className="w-full text-sm text-slate-800 bg-transparent resize-none outline-none placeholder-slate-400 leading-relaxed"
                  style={{ maxHeight: 120, overflowY: "auto" }}
                />
              </div>
              <button
                type="button"
                onClick={handleSend}
                disabled={!draft.trim() || sending || finished}
                className="h-10 w-10 rounded-full flex items-center justify-center transition-all shadow-sm disabled:opacity-40 flex-shrink-0 hover:scale-105 active:scale-95"
                style={{ background: primaryColor }}
              >
                <Send className="h-4 w-4 text-white" style={{ marginLeft: 1 }} />
              </button>
            </div>
            <div className="flex items-center justify-center gap-1 mt-2 text-[10px] text-slate-400">
              <Lock className="h-2.5 w-2.5" />
              <span>Chat seguro - <strong className="text-slate-500 font-semibold">Latus CRM</strong></span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}