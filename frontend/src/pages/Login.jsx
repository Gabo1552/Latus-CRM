import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Eye, EyeOff } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import api from "@/lib/api";
import { LOGIN } from "@/constants/testIds/auth";

const inputClass = "w-full rounded-lg border border-white/65 bg-latus-ink/45 px-4 py-3.5 text-sm text-white placeholder:text-white/35 shadow-inner shadow-black/10 transition-all focus:border-latus-blue focus:outline-none focus:ring-2 focus:ring-latus-blue/40";
const labelClass = "text-sm font-medium text-white/90";

export default function Login() {
  const { login } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [forgotEmail, setForgotEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmittingForgot, setIsSubmittingForgot] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [showForgot, setShowForgot] = useState(false);
  const resetToken = searchParams.get("reset_token");
  const isResetMode = !!resetToken;
  const pwdValid = useMemo(() => /^(?=.*[A-Z])(?=.*\d).{8,}$/.test(newPassword), [newPassword]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!email || !password) {
      toast.error("Por favor completa todos los campos");
      return;
    }
    setIsLoading(true);
    try {
      await login(email, password);
      toast.success("¡Bienvenido!");
    } catch (error) {
      console.error(error);
      toast.error(error.response?.data?.detail || "Credenciales inválidas o error de conexión");
    } finally {
      setIsLoading(false);
    }
  };

  const handleForgot = async (event) => {
    event.preventDefault();
    if (!forgotEmail) {
      toast.error("Ingresá tu email");
      return;
    }
    setIsSubmittingForgot(true);
    try {
      const response = await api.post("/auth/password/forgot", { email: forgotEmail });
      toast.success(response.data?.message || "Si el email existe, te enviamos instrucciones.");
      setShowForgot(false);
      setForgotEmail("");
    } catch (error) {
      toast.error(error.response?.data?.detail || "No se pudo iniciar la recuperación");
    } finally {
      setIsSubmittingForgot(false);
    }
  };

  const handleResetPassword = async (event) => {
    event.preventDefault();
    if (!pwdValid) {
      toast.error("La contraseña debe tener al menos 8 caracteres, una mayúscula y un número");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error("Las contraseñas no coinciden");
      return;
    }
    setIsResetting(true);
    try {
      await api.post("/auth/password/reset", { token: resetToken, new_password: newPassword });
      toast.success("Contraseña actualizada. Ya podés iniciar sesión.");
      setNewPassword("");
      setConfirmPassword("");
      setSearchParams({});
    } catch (error) {
      toast.error(error.response?.data?.detail || "No se pudo restablecer la contraseña");
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <div className="latus-auth-shell latus-grain relative min-h-screen overflow-hidden text-latus-ink">
      <div className="pointer-events-none absolute -left-28 -top-36 h-[30rem] w-[30rem] rounded-full bg-latus-coral/90 blur-[70px]" />
      <div className="pointer-events-none absolute -bottom-52 -right-40 h-[38rem] w-[38rem] rounded-full bg-latus-coral/95 blur-[85px]" />
      <div className="pointer-events-none absolute left-[12%] top-[8%] h-48 w-48 rounded-full bg-white/80 blur-[70px]" />

      <main className="relative z-10 mx-auto grid min-h-screen max-w-[1540px] items-center gap-12 px-6 py-12 sm:px-10 lg:grid-cols-[1.08fr_.92fr] lg:px-20 lg:py-16 xl:gap-20">
        <section className="max-w-3xl lg:pb-6">
          <div className="mb-10 inline-flex items-baseline gap-2 rounded-full bg-latus-ink px-7 py-3 text-latus-ice shadow-[0_14px_30px_rgba(13,31,42,0.12)]">
            <span className="latus-wordmark text-4xl leading-none">Latus</span>
            <span className="text-3xl font-light leading-none">CRM</span>
          </div>

          <h1 className="max-w-[740px] text-[3.5rem] font-light leading-[0.96] tracking-[-0.055em] text-latus-ink sm:text-[4.6rem] xl:text-[5.5rem]">
            Cerrá <span className="latus-editorial font-bold">más ventas</span>
            <br />desde WhatsApp
          </h1>
          <p className="mt-7 max-w-2xl text-base leading-relaxed text-latus-ink/65 sm:text-lg">
            Conversaciones, oportunidades y respuestas asistidas por IA en un solo espacio de ventas.
          </p>

          <div className="mt-8 flex max-w-3xl flex-wrap gap-2.5 text-sm text-latus-ink/65 sm:text-base">
            {["Pipeline en vivo", "Respuestas automatizadas con IA", "Bandeja unificada", "Catálogo"].map((feature) => (
              <span key={feature} className="rounded-full border border-latus-ink/75 bg-latus-surface/35 px-4 py-1.5 backdrop-blur-[1px]">
                {feature}
              </span>
            ))}
          </div>
        </section>

        <section className="w-full max-w-[590px] justify-self-end rounded-[24px] bg-gradient-to-br from-latus-ink to-latus-ink-soft p-7 text-white shadow-[0_28px_70px_rgba(13,31,42,0.18)] sm:p-10 xl:p-12">
          <div className="mb-9 text-center">
            <h2 className="text-3xl font-light tracking-[-0.035em] sm:text-4xl">
              {isResetMode ? (
                "Creá una nueva contraseña"
              ) : (
                <>Ingresá a <span className="latus-editorial text-latus-ice">Latus</span> <span className="text-latus-ice">CRM</span></>
              )}
            </h2>
            <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-white/55">
              {isResetMode
                ? "Definí tu nueva contraseña para recuperar el acceso al CRM."
                : "Accedé a tu espacio de ventas y retomá cada conversación donde la dejaste."}
            </p>
          </div>

          {isResetMode ? (
            <form onSubmit={handleResetPassword} className="space-y-5">
              <div className="space-y-2">
                <label htmlFor="reset-password" className={labelClass}>Nueva contraseña</label>
                <input
                  id="reset-password"
                  type="password"
                  required
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  data-testid={LOGIN.resetPasswordInput}
                  placeholder="Mín. 8 caracteres, 1 mayúscula, 1 número"
                  className={inputClass}
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="reset-password-confirm" className={labelClass}>Confirmar contraseña</label>
                <input
                  id="reset-password-confirm"
                  type="password"
                  required
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  data-testid={LOGIN.resetPasswordConfirmInput}
                  placeholder="Repetí la contraseña"
                  className={inputClass}
                />
              </div>
              <button
                type="submit"
                disabled={isResetting}
                data-testid={LOGIN.resetPasswordSubmitButton}
                className="mt-3 flex w-full items-center justify-center rounded-lg bg-latus-blue py-3.5 font-semibold text-white transition-colors hover:bg-latus-blue-deep disabled:bg-white/25"
              >
                {isResetting ? <span className="h-5 w-5 animate-spin rounded-full border-2 border-white border-t-transparent" /> : "Guardar nueva contraseña"}
              </button>
              <button type="button" onClick={() => setSearchParams({})} className="text-sm font-semibold text-latus-ice hover:text-white">
                Volver al inicio de sesión
              </button>
            </form>
          ) : (
            <>
              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="space-y-2">
                  <label htmlFor="email" className={labelClass}>Correo electrónico</label>
                  <input
                    id="email"
                    type="email"
                    required
                    autoComplete="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    data-testid={LOGIN.emailInput}
                    placeholder="tu-email@empresa.com"
                    className={inputClass}
                  />
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-4">
                    <label htmlFor="password" className={labelClass}>Contraseña</label>
                    <button
                      type="button"
                      data-testid={LOGIN.forgotPasswordLink}
                      onClick={() => setShowForgot((previous) => !previous)}
                      className="text-xs font-semibold text-latus-ice transition-colors hover:text-white"
                    >
                      Olvidé mi contraseña
                    </button>
                  </div>
                  <div className="relative">
                    <input
                      id="password"
                      type={showPassword ? "text" : "password"}
                      required
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      data-testid={LOGIN.passwordInput}
                      placeholder="••••••••"
                      className={`${inputClass} pr-12`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((previous) => !previous)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-white/45 transition-colors hover:text-white"
                      aria-label={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
                    >
                      {showPassword ? <EyeOff className="h-[18px] w-[18px]" /> : <Eye className="h-[18px] w-[18px]" />}
                    </button>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={isLoading}
                  data-testid={LOGIN.submitButton}
                  className="mt-3 flex w-full items-center justify-center rounded-lg bg-latus-blue py-3.5 font-semibold text-white shadow-[0_12px_24px_rgba(25,151,215,0.18)] transition-colors hover:bg-latus-blue-deep disabled:bg-white/25"
                >
                  {isLoading ? <span className="h-5 w-5 animate-spin rounded-full border-2 border-white border-t-transparent" /> : "Iniciar sesión"}
                </button>
              </form>

              {showForgot && (
                <form onSubmit={handleForgot} className="mt-6 space-y-3 rounded-xl border border-white/15 bg-white/[0.06] p-4">
                  <p className="font-semibold text-white">Recuperar acceso</p>
                  <p className="text-xs leading-relaxed text-white/55">Te mandamos un enlace para crear una nueva contraseña.</p>
                  <input
                    type="email"
                    value={forgotEmail}
                    onChange={(event) => setForgotEmail(event.target.value)}
                    data-testid={LOGIN.forgotPasswordEmailInput}
                    placeholder="tu-email@empresa.com"
                    className={inputClass}
                  />
                  <button
                    type="submit"
                    data-testid={LOGIN.forgotPasswordSubmitButton}
                    disabled={isSubmittingForgot}
                    className="w-full rounded-lg bg-latus-blue py-3 text-sm font-semibold text-white transition-colors hover:bg-latus-blue-deep disabled:bg-white/25"
                  >
                    {isSubmittingForgot ? "Enviando…" : "Enviar email de recuperación"}
                  </button>
                </form>
              )}
            </>
          )}

          <p className="mt-7 text-xs leading-relaxed text-white/40">
            El primer usuario en ingresar se convierte en administrador del espacio. Los datos de demostración ya están cargados.
          </p>
        </section>
      </main>
    </div>
  );
}
