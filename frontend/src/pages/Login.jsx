import { MessageSquare, TrendingUp, Zap, ArrowRight } from "lucide-react";

export default function Login() {
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  const handleLogin = () => {
    const redirectUrl = window.location.origin + "/dashboard";
    window.location.href = atob("aHR0cHM6Ly9hdXRoLmVtZXJnZW50YWdlbnQuY29tLz9yZWRpcmVjdD0=") + encodeURIComponent(redirectUrl);
  };

  return (
    <div className="min-h-screen flex bg-[#F9F9F7]">
      {/* Left: brand panel */}
      <div className="hidden lg:flex flex-col justify-between w-[44%] bg-[#0B1B26] p-12 relative overflow-hidden">
        <div
          className="absolute inset-0 opacity-40 mix-blend-screen"
          style={{
            backgroundImage:
              "url(https://images.unsplash.com/photo-1707324148764-99647364afa3?crop=entropy&cs=srgb&fm=jpg&w=900&q=80)",
            backgroundSize: "cover",
            backgroundPosition: "center",
          }}
        />
        <div className="relative z-10 flex items-center gap-2">
          <div className="h-9 w-9 bg-[#0E8DDB] flex items-center justify-center rounded-sm">
            <MessageSquare className="h-5 w-5 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-white font-extrabold text-xl tracking-tighter">Latus CRM</span>
        </div>

        <div className="relative z-10 space-y-6">
          <h1 className="text-white text-5xl font-extrabold tracking-tighter leading-[1.05]">
            Cerrá más ventas<br />desde WhatsApp.
          </h1>
          <p className="text-latus-muted text-base max-w-sm leading-relaxed">
            El centro de comando para equipos de ventas. Gestioná leads, conversaciones, pipeline y respuestas asistidas por IA en un solo lugar.
          </p>
          <div className="flex gap-6 pt-4">
            {[
              { icon: TrendingUp, label: "Pipeline en vivo" },
              { icon: MessageSquare, label: "Bandeja unificada" },
              { icon: Zap, label: "Respuestas IA" },
            ].map((f) => (
              <div key={f.label} className="flex items-center gap-2 text-zinc-300 text-sm">
                <f.icon className="h-4 w-4 text-[#0E8DDB]" />
                {f.label}
              </div>
            ))}
          </div>
        </div>
        <div className="relative z-10 text-zinc-600 text-xs tracking-[0.2em] uppercase font-bold">
          CRM de ventas por WhatsApp
        </div>
      </div>

      {/* Right: login */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2 mb-10">
            <div className="h-9 w-9 bg-[#0E8DDB] flex items-center justify-center rounded-sm">
              <MessageSquare className="h-5 w-5 text-white" strokeWidth={2.5} />
            </div>
            <span className="font-extrabold text-xl tracking-tighter">Latus CRM</span>
          </div>

          <p className="text-xs tracking-[0.2em] uppercase font-bold text-[#888888] mb-3">Bienvenido de nuevo</p>
          <h2 className="text-3xl font-extrabold tracking-tighter text-[#0B1B26] mb-2">Ingresá a Latus CRM</h2>
          <p className="text-sm text-[#888888] mb-8 leading-relaxed">
            Usá tu cuenta de Google para acceder a tu espacio de ventas.
          </p>

          <button
            onClick={handleLogin}
            data-testid="google-login-button"
            className="group w-full flex items-center justify-center gap-3 bg-[#0B1B26] hover:bg-[#0E8DDB] text-white font-semibold py-3.5 rounded-sm transition-colors"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24">
              <path fill="#fff" d="M12 11v2.8h6.6c-.3 1.6-2 4.6-6.6 4.6-4 0-7.2-3.3-7.2-7.4S8 3.6 12 3.6c2.3 0 3.8.9 4.7 1.8l2.2-2.2C17.5 1.7 15 0.6 12 0.6 6.5.6 2 5.1 2 11s4.5 10.4 10 10.4c5.8 0 9.6-4 9.6-9.7 0-.7-.1-1.2-.2-1.7H12z" />
            </svg>
            Continuar con Google
            <ArrowRight className="h-4 w-4 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
          </button>

          <p className="text-xs text-[#888888] mt-6 leading-relaxed">
            El primer usuario en ingresar se convierte en <span className="font-semibold text-[#0B1B26]">Administrador</span> del espacio. Los datos de demostración ya están cargados.
          </p>
        </div>
      </div>
    </div>
  );
}
