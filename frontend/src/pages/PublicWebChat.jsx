import { useState, useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import { Send, Bot, Sparkles, MessageSquare, ShieldCheck, RefreshCw } from "lucide-react";
import axios from "axios";

const rawUrl = (process.env.REACT_APP_BACKEND_URL || "http://localhost:8000").replace(/\/$/, "");
const API_URL = rawUrl.endsWith("/api") ? rawUrl : `${rawUrl}/api`;

const publicApi = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

export default function PublicWebChat() {
  const { token } = useParams();
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

  // Initialize or load session
  useEffect(() => {
    let active = true;
    const initSession = async () => {
      try {
        const res = await publicApi.post("/public/webchat/session", {
          session_token: token,
        });
        if (active) {
          setSession(res.data);
          setMessages(res.data.messages || []);
          setLoading(false);
        }
      } catch (err) {
        if (active) {
          setError("No se pudo cargar la sesión del chat web");
          setLoading(false);
        }
      }
    };
    initSession();
    return () => { active = false; };
  }, [token]);

  // Polling for new messages every 3s
  useEffect(() => {
    if (!token || loading) return;
    const interval = setInterval(async () => {
      try {
        const res = await publicApi.get(`/public/webchat/${token}/messages`);
        if (res.data?.messages) {
          setMessages(res.data.messages);
        }
      } catch (e) {
        // silent fail on poll
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [token, loading]);

  // Scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length]);

  const [finishing, setFinishing] = useState(false);
  const [finished, setFinished] = useState(false);

  const handleFinish = async () => {
    if (finishing || finished) return;
    setFinishing(true);
    try {
      await publicApi.post(`/public/webchat/${token}/finish`);
      setFinished(true);
    } catch (e) {
      // silent
    } finally {
      setFinishing(false);
    }
  };

  const handleSend = async () => {
    if (!draft.trim() || sending) return;
    const text = draft.trim();
    setDraft("");
    setSending(true);

    // Optimistic user message
    const tempMsg = {
      id: `temp_${Date.now()}`,
      sender_type: "contact",
      sender_name: session?.contact_name || "Tú",
      body: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempMsg]);

    try {
      const res = await publicApi.post(`/public/webchat/${token}/messages`, {
        body: text,
        sender_name: session?.contact_name || "Visitante Web",
      });
      if (res.data?.messages) {
        setMessages(res.data.messages);
      }
    } catch (err) {
      // rollback or show error
    } finally {
      setSending(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#F8FAFC] flex flex-col items-center justify-center p-4">
        <div className="h-10 w-10 border-3 border-[#0E8DDB] border-t-transparent rounded-full animate-spin mb-3" />
        <p className="text-sm font-semibold text-slate-600">Cargando chat web…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-[#F8FAFC] flex flex-col items-center justify-center p-4">
        <div className="max-w-md w-full bg-white border border-slate-200 rounded-xl p-6 text-center shadow-sm">
          <MessageSquare className="h-12 w-12 text-slate-300 mx-auto mb-3" />
          <h2 className="text-lg font-bold text-slate-900">Enlace no válido o caducado</h2>
          <p className="text-xs text-slate-500 mt-1">No pudimos conectar con esta sesión de chat web.</p>
        </div>
      </div>
    );
  }

  const primaryColor = session?.webchat_primary_color || "#0E8DDB";
  const title = session?.webchat_title || "Asistente Latus";
  const welcomeMsg = session?.webchat_welcome_message || "¡Hola! ¿En qué puedo ayudarte hoy?";

  return (
    <div className="min-h-screen bg-[#F1F5F9] flex flex-col items-center justify-center sm:p-4 font-sans">
      <div className="w-full max-w-2xl h-screen sm:h-[88vh] bg-white sm:rounded-2xl border-0 sm:border border-slate-200 shadow-xl flex flex-col overflow-hidden">
        
        {/* Header */}
        <div
          className="px-5 py-3.5 flex items-center justify-between text-white shrink-0 shadow-sm"
          style={{ backgroundColor: primaryColor }}
        >
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center text-white font-bold shrink-0 shadow-inner">
              <Bot className="h-5 w-5" />
            </div>
            <div>
              <h1 className="font-bold text-base leading-tight">{title}</h1>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-xs text-white/90 font-medium">En línea · Respuesta instantánea</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleFinish}
              disabled={finishing || finished}
              className="text-xs font-bold bg-white/20 hover:bg-white/30 px-3 py-1.5 rounded-lg text-white transition-all flex items-center gap-1 shadow-2xs"
              title="Finalizar consulta y recibir resumen por WhatsApp"
            >
              {finishing ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
              {finished ? "✓ Finalizado" : "Finalizar Chat"}
            </button>
            <div className="hidden sm:flex items-center gap-1 text-[11px] font-semibold bg-white/15 px-2.5 py-1.5 rounded-lg text-white/90">
              <ShieldCheck className="h-3.5 w-3.5" /> Chat seguro
            </div>
          </div>
        </div>

        {/* Messages Body */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-3.5 bg-[#F8FAFC]"
        >
          {finished && (
            <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3.5 text-center text-xs font-semibold text-emerald-900 shadow-2xs">
              ✓ Consulta finalizada. Te enviamos un resumen con conclusiones a tu WhatsApp.
            </div>
          )}

          {/* Welcome Message */}
          <div className="flex justify-start">
            <div className="max-w-[82%] rounded-2xl rounded-tl-sm px-4 py-3 bg-white border border-slate-200 text-slate-800 shadow-2xs">
              <div className="flex items-center gap-1 text-[11px] font-bold text-[#0E8DDB] uppercase tracking-wider mb-1">
                <Sparkles className="h-3 w-3" /> {session?.bot_name || "Bot"}
              </div>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{welcomeMsg}</p>
            </div>
          </div>

          {/* Dynamic Messages */}
          {messages.map((m) => {
            const isCustomer = m.sender_type === "contact";
            const isBot = m.sender_type === "bot";
            const isSystem = m.sender_type === "system";

            if (isSystem) {
              return (
                <div key={m.id} className="flex justify-center my-2">
                  <span className="text-[11px] font-semibold text-slate-500 bg-slate-100 border border-slate-200 px-3 py-0.5 rounded-full">
                    {m.body}
                  </span>
                </div>
              );
            }

            return (
              <div key={m.id} className={`flex ${isCustomer ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[82%] rounded-2xl px-4 py-2.5 shadow-2xs ${
                    isCustomer
                      ? "rounded-tr-sm text-white"
                      : isBot
                      ? "rounded-tl-sm bg-white border border-slate-200 text-slate-900"
                      : "rounded-tl-sm bg-slate-100 border border-slate-200 text-slate-900"
                  }`}
                  style={isCustomer ? { backgroundColor: primaryColor } : {}}
                >
                  {!isCustomer && (
                    <div className="flex items-center gap-1 text-[10px] font-bold text-[#0E8DDB] uppercase tracking-wider mb-0.5">
                      <Bot className="h-3 w-3" /> {isBot ? (session?.bot_name || "Bot IA") : m.sender_name}
                    </div>
                  )}
                  <p className="text-sm leading-relaxed whitespace-pre-wrap">{m.body}</p>
                  <div
                    className={`mt-1 text-[10px] text-right font-medium ${
                      isCustomer ? "text-white/80" : "text-slate-400"
                    }`}
                  >
                    {m.created_at ? new Date(m.created_at).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" }) : ""}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Composer */}
        <div className="p-3.5 bg-white border-t border-slate-200 shrink-0">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Escribí tu mensaje aquí…"
              className="flex-1 h-11 px-4 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#0E8DDB] focus:bg-white transition-all"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!draft.trim() || sending}
              className="h-11 px-4 text-white font-bold rounded-xl flex items-center justify-center transition-all disabled:opacity-50 shadow-2xs"
              style={{ backgroundColor: primaryColor }}
            >
              {sending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </button>
          </div>
          <p className="text-[10px] text-center text-slate-400 mt-2 font-medium">
            Potenciado por <strong className="text-slate-600">Latus CRM</strong> · Chat seguro y privado
          </p>
        </div>

      </div>
    </div>
  );
}
