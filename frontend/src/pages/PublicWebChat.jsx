import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Send, CheckCheck, Lock, PhoneOff } from "lucide-react";
import axios from "axios";

const rawUrl = (process.env.REACT_APP_BACKEND_URL || "http://localhost:8000").replace(/\/$/, "");
const API_URL = rawUrl.endsWith("/api") ? rawUrl : `${rawUrl}/api`;

const publicApi = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

const publicMessagesOnly = (items = []) => items.filter(
  (item) => ["contact", "bot", "agent"].includes(item?.sender_type)
);

function TypingIndicator({ color, botName }) {
  return (
    <div className="flex justify-start items-end gap-2 px-1">
      <div
        className="h-8 w-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs font-bold shadow-sm"
        style={{ background: color }}
      >
        IA
      </div>
      <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-4 py-2.5 shadow-sm">
        <p className="mb-1 text-[10px] font-semibold text-slate-500">{botName || "El asistente"} está escribiendo…</p>
        <div className="flex items-center gap-1" aria-label="El asistente está escribiendo">
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
  const isAgent = msg.sender_type === "agent";
  const isSystem = msg.sender_type === "system";

  const messageDate = msg.created_at ? new Date(msg.created_at) : null;
  const isToday = messageDate && new Date().toDateString() === messageDate.toDateString();
  const timeStr = messageDate
    ? messageDate.toLocaleString("es-AR", {
        ...(isToday ? {} : { day: "2-digit", month: "2-digit" }),
        hour: "2-digit", minute: "2-digit",
      })
    : "";

  if (isSystem) {
    return (
      <div className="flex justify-center my-1">
        <span className="text-[11px] font-medium text-slate-400 bg-white/70 border border-slate-200 px-3 py-1 rounded-full">
          {msg.body}{timeStr ? ` · ${timeStr}` : ""}
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
        {(isBot || isAgent) && (
          <span className="text-[10px] font-semibold text-slate-500 mb-0.5 px-1">
            {isAgent ? (msg.sender_name || "Asesor") : botName}
          </span>
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
            {isContact && <><span>Enviado</span><CheckCheck className="h-3 w-3" /></>}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function PublicWebChat() {
  const { token } = useParams();
  const [searchParams] = useSearchParams();
  const organizationKey = searchParams.get("key") || "";
  const [activeToken, setActiveToken] = useState(token === "nuevo" ? "" : token);
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [finished, setFinished] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [botTyping, setBotTyping] = useState(false);
  const [actionError, setActionError] = useState("");
  const [summarySent, setSummarySent] = useState(false);
  const [retryMessageId, setRetryMessageId] = useState("");

  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const prevMsgCount = useRef(0);
  const replyCountBeforeSend = useRef(0);

  const scrollToBottom = useCallback((behavior = "smooth") => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior });
    }
  }, []);

  useEffect(() => {
    let active = true;
    const init = async () => {
      try {
        if (token === "nuevo" && !organizationKey) throw new Error("missing organization key");
        const res = await publicApi.post("/public/webchat/session", {
          session_token: token === "nuevo" ? undefined : token,
          organization_key: organizationKey || undefined,
        });
        if (active) {
          setSession(res.data);
          setMessages(publicMessagesOnly(res.data.messages));
          setActiveToken(res.data.session_token);
          setFinished(!!res.data.finished);
          if (token === "nuevo" && res.data.session_token) {
            window.history.replaceState({}, "", `/c/${res.data.session_token}`);
          }
          setLoading(false);
        }
      } catch {
        if (active) { setError(true); setLoading(false); }
      }
    };
    init();
    return () => { active = false; };
  }, [token, organizationKey]);

  useEffect(() => {
    if (!activeToken || loading || finished) return;
    const interval = setInterval(async () => {
      if (document.hidden || sending) return;
      try {
        const res = await publicApi.get(`/public/webchat/${activeToken}/messages`);
        if (res.data?.messages) {
          const newMsgs = publicMessagesOnly(res.data.messages);
          setMessages(prev => {
            return newMsgs;
          });
          setSession(prev => ({ ...prev, bot_enabled: res.data.bot_enabled, bot_status: res.data.bot_status }));
          const replyCount = newMsgs.filter((item) => item.sender_type === "bot" || item.sender_type === "agent").length;
          const needsHuman = res.data.bot_enabled === false || ["requiere_humano", "en_atencion_humana", "cerrada"].includes(res.data.bot_status);
          if (replyCount > replyCountBeforeSend.current || needsHuman) setBotTyping(false);
          if (res.data.finished) setFinished(true);
        }
      } catch { /* silent */ }
    }, botTyping ? 1500 : 4000);
    return () => clearInterval(interval);
  }, [activeToken, loading, finished, sending, botTyping]);

  useEffect(() => {
    if (messages.length > prevMsgCount.current) scrollToBottom();
    prevMsgCount.current = messages.length;
  }, [messages.length, scrollToBottom]);

  const handleDraftChange = (e) => {
    setDraft(e.target.value);
    setRetryMessageId("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + "px";
    }
  };

  const handleSend = async () => {
    if (!draft.trim() || sending || finished || !activeToken) return;
    const text = draft.trim();
    setDraft("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setSending(true);
    setActionError("");
    const clientMessageId = retryMessageId || crypto.randomUUID();
    replyCountBeforeSend.current = messages.filter(
      (item) => item.sender_type === "bot" || item.sender_type === "agent"
    ).length;
    setBotTyping(true);
    const tempMsg = {
      id: `temp_${clientMessageId}`,
      sender_type: "contact",
      sender_name: session?.contact_name || "Tu",
      body: text,
      created_at: new Date().toISOString(),
      delivery_status: "delivered",
    };
    setMessages(prev => [...prev, tempMsg]);
    scrollToBottom();
    try {
      const res = await publicApi.post(`/public/webchat/${activeToken}/messages`, {
        body: text,
        sender_name: session?.contact_name || "Visitante Web",
        client_message_id: clientMessageId,
      });
      if (res.data?.messages) {
        setMessages(prev => {
          const merged = new Map(
            prev.filter(item => item.id !== tempMsg.id).map(item => [item.id, item])
          );
          publicMessagesOnly(res.data.messages).forEach(item => merged.set(item.id, item));
          return Array.from(merged.values()).sort(
            (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0)
          );
        });
        setSession(prev => ({
          ...prev,
          bot_enabled: res.data.bot_enabled,
          bot_status: res.data.bot_status,
        }));
        const needsHuman = res.data.bot_enabled === false || ["requiere_humano", "en_atencion_humana", "cerrada"].includes(res.data.bot_status);
        setBotTyping(!needsHuman && res.data.processing !== false);
        setRetryMessageId("");
      }
    } catch (e) {
      setBotTyping(false);
      setMessages(prev => prev.filter(item => item.id !== tempMsg.id));
      setDraft(text);
      setRetryMessageId(clientMessageId);
      setActionError(e?.response?.data?.detail || "No pudimos enviar el mensaje. Intentá nuevamente.");
    } finally {
      setSending(false);
    }
  };

  const handleFinish = async () => {
    if (finishing || finished || !activeToken) return;
    setFinishing(true);
    setActionError("");
    try {
      const res = await publicApi.post(`/public/webchat/${activeToken}/finish`);
      setSummarySent(!!res.data?.summary_sent);
      setFinished(true);
    } catch (e) {
      setActionError(e?.response?.data?.detail || "No pudimos finalizar la consulta.");
    }
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
          <h2 className="text-lg font-bold text-slate-800">Sesión no encontrada</h2>
          <p className="text-sm text-slate-500 mt-1.5">Este enlace puede haber expirado o ser inválido.</p>
        </div>
      </div>
    );
  }

  const primaryColor = session?.webchat_primary_color || "#0E8DDB";
  const bgColor = session?.webchat_bg_color || "#EFF2F5";
  const title = session?.webchat_title || "Asistente";
  const welcomeMsg = session?.webchat_welcome_message || "¡Hola! ¿En qué puedo ayudarte hoy?";
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
                <span className="text-white/85 text-[11px] font-medium">
                  {finished ? "Consulta finalizada" : session?.bot_enabled === false ? "Te atenderá una persona" : botTyping ? "Escribiendo una respuesta…" : "Asistente disponible"}
                </span>
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
                  {summarySent ? "Consulta finalizada. Enviamos el resumen a tu WhatsApp." : "Consulta finalizada correctamente."}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <ChatMessage key={m.id} msg={m} session={session} isLatest={i === messages.length - 1} />
            ))}

            {botTyping && <TypingIndicator color={primaryColor} botName={botName} />}
            {actionError && (
              <div className="mx-auto max-w-sm rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-center text-xs font-medium text-rose-700">
                {actionError}
              </div>
            )}
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
                  maxLength={2000}
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
