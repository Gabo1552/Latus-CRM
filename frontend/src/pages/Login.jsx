import { MessageSquare, TrendingUp, Zap, ArrowRight } from "lucide-react";

export default function Login() {
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  const handleLogin = () => {
    const redirectUrl = window.location.origin + "/dashboard";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  return (
    <div className="min-h-screen flex bg-[#F9F9F7]">
      {/* Left: brand panel */}
      <div className="hidden lg:flex flex-col justify-between w-[44%] bg-[#0A0A0A] p-12 relative overflow-hidden">
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
          <div className="h-9 w-9 bg-[#FF4500] flex items-center justify-center rounded-sm">
            <MessageSquare className="h-5 w-5 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-white font-extrabold text-xl tracking-tighter">FlowDesk</span>
        </div>

        <div className="relative z-10 space-y-6">
          <h1 className="text-white text-5xl font-extrabold tracking-tighter leading-[1.05]">
            Close more deals<br />from WhatsApp.
          </h1>
          <p className="text-zinc-400 text-base max-w-sm leading-relaxed">
            The command center for sales teams. Manage leads, conversations, pipeline and AI-assisted replies in one place.
          </p>
          <div className="flex gap-6 pt-4">
            {[
              { icon: TrendingUp, label: "Live pipeline" },
              { icon: MessageSquare, label: "Unified inbox" },
              { icon: Zap, label: "AI replies" },
            ].map((f) => (
              <div key={f.label} className="flex items-center gap-2 text-zinc-300 text-sm">
                <f.icon className="h-4 w-4 text-[#FF4500]" />
                {f.label}
              </div>
            ))}
          </div>
        </div>
        <div className="relative z-10 text-zinc-600 text-xs tracking-[0.2em] uppercase font-bold">
          WhatsApp Sales CRM
        </div>
      </div>

      {/* Right: login */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2 mb-10">
            <div className="h-9 w-9 bg-[#FF4500] flex items-center justify-center rounded-sm">
              <MessageSquare className="h-5 w-5 text-white" strokeWidth={2.5} />
            </div>
            <span className="font-extrabold text-xl tracking-tighter">FlowDesk</span>
          </div>

          <p className="text-xs tracking-[0.2em] uppercase font-bold text-[#52525B] mb-3">Welcome back</p>
          <h2 className="text-3xl font-extrabold tracking-tighter text-[#0A0A0A] mb-2">Sign in to FlowDesk</h2>
          <p className="text-sm text-[#52525B] mb-8 leading-relaxed">
            Use your Google account to access your sales workspace.
          </p>

          <button
            onClick={handleLogin}
            data-testid="google-login-button"
            className="group w-full flex items-center justify-center gap-3 bg-[#0A0A0A] hover:bg-[#FF4500] text-white font-semibold py-3.5 rounded-sm transition-colors"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24">
              <path fill="#fff" d="M12 11v2.8h6.6c-.3 1.6-2 4.6-6.6 4.6-4 0-7.2-3.3-7.2-7.4S8 3.6 12 3.6c2.3 0 3.8.9 4.7 1.8l2.2-2.2C17.5 1.7 15 0.6 12 0.6 6.5.6 2 5.1 2 11s4.5 10.4 10 10.4c5.8 0 9.6-4 9.6-9.7 0-.7-.1-1.2-.2-1.7H12z" />
            </svg>
            Continue with Google
            <ArrowRight className="h-4 w-4 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
          </button>

          <p className="text-xs text-[#52525B] mt-6 leading-relaxed">
            First user to sign in becomes the workspace <span className="font-semibold text-[#0A0A0A]">Admin</span>. Demo data is preloaded.
          </p>
        </div>
      </div>
    </div>
  );
}
