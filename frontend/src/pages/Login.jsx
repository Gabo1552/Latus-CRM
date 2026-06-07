import { useState } from "react";
import { MessageSquare, TrendingUp, Zap, ArrowRight, Eye, EyeOff } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { LOGIN } from "@/constants/testIds/auth";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email || !password) {
      toast.error("Por favor completa todos los campos");
      return;
    }
    setIsLoading(true);
    try {
      await login(email, password);
      toast.success("¡Bienvenido!");
    } catch (err) {
      console.error(err);
      toast.error(err.response?.data?.detail || "Credenciales inválidas o error de conexión");
    } finally {
      setIsLoading(false);
    }
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
      <div className="flex-1 flex items-center justify-center p-8 bg-[#F9F9F7]">
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
            Ingresá tus credenciales para acceder a tu espacio de ventas.
          </p>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-1.5">
              <label htmlFor="email" className="text-xs font-bold uppercase tracking-wider text-[#0B1B26]">
                Correo electrónico
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                data-testid={LOGIN.emailInput}
                placeholder="ejemplo@latus.test"
                className="w-full px-3.5 py-2.5 border border-[#D8D4C7] rounded-sm bg-white text-[#0B1B26] placeholder:text-[#888888] focus:outline-none focus:ring-2 focus:ring-[#0E8DDB] focus:border-transparent transition-all text-sm"
              />
            </div>

            <div className="space-y-1.5">
              <div className="flex justify-between items-center">
                <label htmlFor="password" className="text-xs font-bold uppercase tracking-wider text-[#0B1B26]">
                  Contraseña
                </label>
              </div>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  data-testid={LOGIN.passwordInput}
                  placeholder="••••••••"
                  className="w-full pl-3.5 pr-10 py-2.5 border border-[#D8D4C7] rounded-sm bg-white text-[#0B1B26] placeholder:text-[#888888] focus:outline-none focus:ring-2 focus:ring-[#0E8DDB] focus:border-transparent transition-all text-sm"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[#888888] hover:text-[#0B1B26] focus:outline-none cursor-pointer"
                >
                  {showPassword ? (
                    <EyeOff className="h-4.5 w-4.5" />
                  ) : (
                    <Eye className="h-4.5 w-4.5" />
                  )}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              data-testid={LOGIN.submitButton}
              className="group w-full flex items-center justify-center gap-3 bg-[#0B1B26] hover:bg-[#0E8DDB] disabled:bg-[#888888] text-white font-semibold py-3.5 rounded-sm transition-colors cursor-pointer"
            >
              {isLoading ? (
                <div className="h-5 w-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <>
                  Iniciar sesión
                  <ArrowRight className="h-4 w-4 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
                </>
              )}
            </button>
          </form>

          <p className="text-xs text-[#888888] mt-6 leading-relaxed">
            El primer usuario en ingresar se convierte en <span className="font-semibold text-[#0B1B26]">Administrador</span> del espacio. Los datos de demostración ya están cargados.
          </p>
        </div>
      </div>
    </div>
  );
}
