import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Store,
  ArrowRight,
  Scissors,
  Package,
  BookOpenText,
  Truck,
  BarChart3,
  Sparkles,
  Check,
  Menu,
  X,
  Receipt,
  MessageCircleQuestion,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext.tsx';
import { ThemeToggle } from '../components/ui/ThemeToggle.tsx';

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

function AuthButtons({ mobile = false }: { mobile?: boolean }) {
  const { isAuthenticated, isLoading, user } = useAuth();
  const navigate = useNavigate();

  if (isLoading) {
    return (
      <div className={`flex ${mobile ? 'flex-col w-full' : 'items-center'} gap-2`}>
        <div className="h-9 w-24 animate-pulse rounded-md bg-zinc-200 dark:bg-zinc-800" />
        <div className="h-9 w-32 animate-pulse rounded-md bg-zinc-200 dark:bg-zinc-800" />
      </div>
    );
  }

  if (isAuthenticated) {
    return (
      <div className={`flex ${mobile ? 'flex-col w-full' : 'items-center'} gap-2`}>
        {!mobile && user?.fullName && (
          <span className="hidden max-w-40 truncate text-xs text-zinc-500 dark:text-zinc-400 lg:block">
            Salaam, <span className="font-semibold text-zinc-800 dark:text-zinc-200">{user.fullName.split(' ')[0]}</span>
          </span>
        )}
        <button
          onClick={() => navigate('/dashboard')}
          className={`clay-btn inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-xl border border-[var(--accent-border)] bg-[var(--clay-surface)] px-4 text-sm font-semibold text-[var(--accent-ink)] transition-colors hover:bg-[var(--accent-soft)] ${mobile ? 'w-full' : ''}`}
        >
          <LayoutDashboardIcon />
          Open Dashboard
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className={`flex ${mobile ? 'flex-col w-full' : 'items-center'} gap-2`}>
      <Link
        to="/login"
        className={`clay-btn inline-flex h-9 items-center justify-center rounded-xl border border-zinc-300 bg-white px-4 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-transparent dark:text-zinc-200 dark:hover:bg-zinc-800 ${mobile ? 'w-full' : ''}`}
      >
        Sign in
      </Link>
      <Link
        to="/signup"
        className={`clay-btn clay-btn-accent inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-[var(--accent)] px-4 text-sm font-semibold text-[var(--accent-on)] ${mobile ? 'w-full' : ''}`}
      >
        Get started free
        <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}

function LayoutDashboardIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <rect width="7" height="9" x="3" y="3" rx="1" />
      <rect width="7" height="5" x="14" y="3" rx="1" />
      <rect width="7" height="9" x="14" y="12" rx="1" />
      <rect width="7" height="5" x="3" y="16" rx="1" />
    </svg>
  );
}

function SectionEyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="clay-badge mb-3 inline-flex items-center gap-2 border border-zinc-200 bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-widest text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
      {children}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Data                                                                */
/* ------------------------------------------------------------------ */

type Feature = {
  icon: typeof Receipt;
  title: string;
  desc: string;
  layout: 'featured' | 'wide' | 'standard' | 'wide-end';
  detail?: 'stock' | 'khata' | 'flow' | 'supplier' | 'pl';
};

const features: Feature[] = [
  {
    icon: Receipt,
    title: 'POS built for kapra',
    desc: 'Meter ya piece, bill seconds mein. Cash, Easypaisa ya udhaar.',
    layout: 'featured',
  },
  {
    icon: Scissors,
    title: 'Thaan se ready suit tak',
    desc: 'Open fabric, 3-piece aur boutique pieces. Har variant alag.',
    layout: 'wide',
    detail: 'flow',
  },
  {
    icon: Package,
    title: 'Inventory jo alert de',
    desc: 'Live stock har variant ka. Kam ho to khabar, bina ginne ke.',
    layout: 'standard',
    detail: 'stock',
  },
  {
    icon: BookOpenText,
    title: 'Customer khata, zero diary',
    desc: 'Udhaar sale khud khate mein chadh jati hai. Recovery tareekh ke saath.',
    layout: 'standard',
    detail: 'khata',
  },
  {
    icon: Truck,
    title: 'Supplier khata & purchases',
    desc: 'Purchase bill, payables aur supplier credit, ek hi flow mein.',
    layout: 'standard',
    detail: 'supplier',
  },
  {
    icon: BarChart3,
    title: 'Real hisaab, guesswork nahi',
    desc: 'P&L, COGS aur net profit. Shaam ka hisaab ek nazar mein.',
    layout: 'wide-end',
    detail: 'pl',
  },
];

const steps = [
  {
    n: '01',
    title: 'Subah — Dashboard kholo',
    desc: 'Aaj ki sale, kharcha, udhaar recovery aur low-stock — sab ek screen par. Din plan ho gaya.',
  },
  {
    n: '02',
    title: 'Din — Sale + Purchase',
    desc: 'New Sale par meter kaat ke bill, New Purchase par stock auto-plus. Khata entries khud ban jati hain.',
  },
  {
    n: '03',
    title: 'Shaam — Reports band karo',
    desc: 'Daily sales, P&L aur AI se poocho “Aaj kitni sale hui?” — hisaab clear, tension khatam.',
  },
];

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export function LandingPage() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();

  const primaryCta = () => navigate(isAuthenticated ? '/dashboard' : '/signup');

  return (
    <div className="min-h-screen bg-[var(--clay-bg)] text-zinc-900 antialiased dark:text-zinc-100">
      {/* ================= NAVBAR ================= */}
      <header className="clay-topbar sticky top-0 z-50">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="clay-icon-tile h-9 w-9 bg-[var(--accent)] text-[var(--accent-on)] border-transparent">
              <Store className="h-4.5 w-4.5" />
            </span>
            <span className="leading-tight">
              <span className="block text-[15px] font-bold tracking-tight">KapraOS</span>
              <span className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400">Apni bahi ko digital karo</span>
            </span>
          </Link>

          <nav className="hidden items-center gap-1 text-sm font-medium text-zinc-600 md:flex dark:text-zinc-300">
            {[
              ['Features', '#features'],
              ['Khata', '#khata'],
              ['Dukaan flow', '#how'],
              ['AI Munshi', '#ai'],
            ].map(([label, href]) => (
              <a key={href} href={href} className="rounded-md px-3 py-2 transition-colors hover:bg-indigo-50 hover:text-indigo-700 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-300">
                {label}
              </a>
            ))}
          </nav>

          <div className="hidden items-center gap-2 md:flex">
            <ThemeToggle />
            <AuthButtons />
          </div>

          <div className="flex items-center gap-1 md:hidden">
            <ThemeToggle />
            <button
              onClick={() => setMenuOpen((v) => !v)}
              aria-label="Toggle menu"
              className="flex h-9 w-9 items-center justify-center rounded-md text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>

        {menuOpen && (
          <div className="border-t border-[var(--clay-border-soft)] bg-[var(--clay-surface)] px-4 py-4 md:hidden">
            <nav className="mb-4 flex flex-col gap-1 text-sm font-medium">
              {[
                ['Features', '#features'],
                ['Khata', '#khata'],
                ['Dukaan flow', '#how'],
                ['AI Munshi', '#ai'],
              ].map(([label, href]) => (
                <a
                  key={href}
                  href={href}
                  onClick={() => setMenuOpen(false)}
                  className="rounded-md px-3 py-2.5 text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                >
                  {label}
                </a>
              ))}
            </nav>
            <AuthButtons mobile />
          </div>
        )}
      </header>

      {/* ================= HERO ================= */}
      <section className="relative lg:flex lg:min-h-[calc(100svh-65px)] lg:items-center">
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[var(--accent)] opacity-[0.035]" />
        <div className="hero-enter relative mx-auto grid w-full max-w-7xl items-start gap-10 px-4 pt-12 pb-20 sm:px-6 sm:pt-16 lg:grid-cols-[1.08fr_0.92fr] lg:items-center lg:gap-14 lg:px-8 lg:py-8">
          {/* copy — headline, one short line, one CTA */}
          <div className="lg:-translate-y-8">
            <h1 className="text-[1.9rem] font-semibold leading-[1.16] tracking-[-0.01em] text-zinc-900 sm:text-[2.6rem] lg:text-[clamp(2.35rem,3.3vw,2.95rem)] dark:text-zinc-100">
              Aapki poori kapra dukaan,
              <span className="block pb-2 text-[var(--accent-ink)]">
                <span className="relative inline-block">
                  ab ek hi screen par.
                  <span
                    aria-hidden="true"
                    className="absolute -bottom-1 left-0 h-0.5 w-full rounded-full bg-[var(--accent)]"
                  />
                </span>
              </span>
            </h1>

            <p className="mt-5 max-w-[31rem] text-base leading-relaxed text-zinc-600 sm:text-[17px] dark:text-zinc-300">
              Thaan ho ya suit, udhaar ya cash. KapraOS stock, sale, khata aur profit sab ek hi jagah rakhta hai.
            </p>

            <div className="mt-7 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-5">
              <button
                onClick={primaryCta}
                disabled={isLoading}
                className="clay-btn clay-btn-accent inline-flex h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-[var(--accent)] px-6 text-[15px] font-semibold text-[var(--accent-on)] disabled:opacity-60 sm:w-auto"
              >
                {isLoading ? 'Loading…' : isAuthenticated ? 'Open Dashboard' : 'Apni dukaan joro'}
                <ArrowRight className="h-4.5 w-4.5" />
              </button>

              {!isLoading && (
                isAuthenticated ? (
                  <a
                    href="#features"
                    className="text-sm font-medium text-zinc-600 underline-offset-4 transition-colors hover:text-zinc-900 hover:underline dark:text-zinc-300 dark:hover:text-zinc-100"
                  >
                    Explore features ↓
                  </a>
                ) : (
                  <Link
                    to="/login"
                    className="text-sm font-medium text-zinc-600 underline-offset-4 transition-colors hover:text-zinc-900 hover:underline dark:text-zinc-300 dark:hover:text-zinc-100"
                  >
                    Already have an account? Sign in
                  </Link>
                )
              )}
            </div>

            <p className="mt-10 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs font-medium text-zinc-700 dark:text-zinc-100">
              <span>POS billing</span>
              <span aria-hidden="true" className="text-zinc-400 dark:text-zinc-500">·</span>
              <span>Udhaar khata</span>
              <span aria-hidden="true" className="text-zinc-400 dark:text-zinc-500">·</span>
              <span>Daily P&amp;L</span>
            </p>
          </div>

          {/* visual — offset boutique photo with two purposeful clay tags */}
          <div className="relative mx-auto w-full max-w-[34rem] pb-12 lg:max-w-none">
            <div className="relative overflow-hidden rounded-[2rem_2rem_1.85rem_0.75rem] border border-[var(--clay-border)] shadow-[0_18px_50px_-24px_rgb(30_34_43_/_0.45)] dark:shadow-[0_24px_60px_-28px_rgb(0_0_0_/_0.75)]">
              <img
                src="/pexels-arina-dmitrieva-66352626-14440412.jpg"
                alt="Boutique suit and woven shawl hanging in a fabric shop — managed in KapraOS"
                className="h-[25rem] w-full object-cover sm:h-[30rem] lg:h-[min(70svh,560px)]"
                loading="eager"
              />
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 rounded-[inherit] shadow-[inset_0_1px_0_rgb(255_255_255_/_0.22),inset_0_-48px_56px_-52px_rgb(20_20_20_/_0.55)]"
              />
            </div>

            {/* price tag — small, overlapping the left edge */}
            <div className="absolute top-7 -left-2 z-10 min-w-[8.5rem] rotate-[-1.5deg] rounded-[14px_14px_5px_14px] border border-[var(--clay-border-soft)] bg-[var(--clay-surface)] px-3.5 py-2.5 shadow-[var(--clay-highlight),var(--clay-shadow-sm)]">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500 dark:text-zinc-400">Lawn 3pc</p>
              <p className="font-tabular text-base font-bold leading-none text-zinc-900 dark:text-zinc-100">Rs 4,850</p>
              <p className="mt-1 text-[10px] text-zinc-500 dark:text-zinc-400">Meter cut · 2.5 m</p>
            </div>

            {/* sale card — larger, bottom-right edge overlap */}
            <div className="absolute -right-4 -bottom-1.5 w-[15.5rem] max-w-[calc(100%-1rem)] rotate-[1deg] rounded-[18px_18px_18px_6px] border border-[var(--clay-border-soft)] bg-[var(--clay-surface)] px-4 py-3 shadow-[var(--clay-highlight),var(--clay-shadow-md)]">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-500 dark:text-zinc-400">Aaj ki sale</p>
                <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 dark:text-emerald-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  Live
                </span>
              </div>
              <p className="mt-1 font-tabular text-xl font-bold leading-none text-zinc-900 dark:text-zinc-100">Rs 86,400</p>
              <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">23 bills · 6 udhaar</p>
            </div>
          </div>
        </div>
      </section>

      {/* ================= PAIN → RELIEF ================= */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="feature-reveal grid items-center gap-10 xl:grid-cols-[0.95fr_1.05fr] xl:gap-12">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--accent-ink)]">
              Dher se system tak
            </p>
            <h2 className="mt-3 text-2xl font-semibold leading-tight tracking-normal sm:text-3xl">
              Kapron ke dher mein{' '}
              <span className="text-[var(--accent-ink)] dark:text-[var(--accent)]">hisaab</span>{' '}
              nahi dhoondna parta.
            </h2>
            <p className="mt-3 max-w-[46ch] text-pretty text-sm leading-relaxed text-zinc-600 dark:text-zinc-400 sm:text-[15px]">
              Roz shaam copy mein sale jorna aur udhaar yaad rakhna, ye thakaan khatam. KapraOS ye
              teenon kaam khud karta hai.
            </p>
            <ul className="mt-5 space-y-2.5">
              {[
                'Har variant ka live stock, color aur size ke saath',
                'Har udhaar customer ke khate mein',
                'Har din ka P&L, ek nazar mein',
              ].map((li) => (
                <li key={li} className="flex items-start gap-2.5 text-sm text-zinc-700 dark:text-zinc-300">
                  <Check
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--accent-ink)] dark:text-[var(--accent)]"
                    strokeWidth={2}
                  />
                  <span>{li}</span>
                </li>
              ))}
            </ul>
            <button
              onClick={primaryCta}
              disabled={isLoading}
              className="clay-btn clay-btn-accent mt-7 inline-flex h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-[var(--accent)] px-6 text-[15px] font-semibold text-[var(--accent-on)] disabled:opacity-60 sm:w-auto"
            >
              {isAuthenticated ? 'Go to Dashboard' : 'Dher khatam karo, Start free'}
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>

          <div className="relative">
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_15rem] sm:items-stretch sm:gap-5">
              <div className="order-1 overflow-hidden clay-card">
                <div className="flex items-center justify-between gap-3 border-b border-[var(--clay-border-soft)] px-4 py-4 sm:px-5">
                  <div>
                    <p className="text-sm font-semibold leading-tight">Aaj ka hisaab</p>
                    <p className="mt-0.5 text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-500 dark:text-zinc-400">
                      Evening closing
                    </p>
                  </div>
                  <span className="inline-flex shrink-0 items-center gap-1.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">
                    <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    Live
                  </span>
                </div>

                <div className="px-4 pt-1 pb-4 sm:px-5">
                  <div className="divide-y divide-dashed divide-[var(--clay-border-soft)]">
                    {[
                      { time: 'Aaj', item: 'Sale, 23 bills', amount: 'Rs 86,400', positive: false, badge: '' },
                      { time: '11:42', item: 'Bill #1042, Lawn 3pc', amount: 'Rs 4,850', positive: false, badge: '' },
                      { time: '02:10', item: 'Ahmed Sahab, udhaar recovery', amount: '+ Rs 15,000', positive: true, badge: '' },
                      { time: '04:30', item: 'Kharcha, chai aur bijli', amount: 'Rs 4,200', positive: false, badge: '' },
                      { time: 'Stock', item: 'Low stock, Lawn Sky Blue', amount: '', positive: false, badge: '4m bacha' },
                    ].map((row) => (
                      <div
                        key={`${row.time}-${row.item}`}
                        className="grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-baseline gap-3 py-2.5"
                      >
                        <span className="font-tabular text-[11px] text-zinc-500 dark:text-zinc-400">{row.time}</span>
                        <p className="min-w-0 text-[13px] leading-snug text-zinc-700 dark:text-zinc-300">{row.item}</p>
                        <div className="text-right">
                          {row.badge ? (
                            <span className="inline-flex rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)]">
                              {row.badge}
                            </span>
                          ) : (
                            <span
                              className={`font-tabular text-[13px] font-semibold ${
                                row.positive
                                  ? 'text-emerald-700 dark:text-emerald-400'
                                  : 'text-zinc-800 dark:text-zinc-100'
                              }`}
                            >
                              {row.amount}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-3 rounded-xl bg-[var(--clay-surface-soft)] px-4 py-3 shadow-[var(--clay-highlight),var(--clay-shadow-sm)]">
                    <p className="text-[13px] font-medium">Net profit, aaj</p>
                    <p className="font-tabular text-[15px] font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)]">
                      Rs 19,750
                    </p>
                  </div>
                </div>
              </div>

              <div className="order-2 flex flex-col sm:min-h-0">
                <figure className="overflow-hidden border border-[var(--clay-border)] bg-[var(--clay-surface)] shadow-[var(--clay-highlight),var(--clay-shadow-md)] rounded-[18px_10px_16px_7px] sm:min-h-0 sm:flex-1">
                  <img
                    src="/pexels-teona-swift-6850486.jpg"
                    alt="Kapron ke dher mein dukandaar, pehle ka haal"
                    className="aspect-[16/10] w-full object-cover object-[72%_60%] sm:aspect-auto sm:h-full"
                    loading="lazy"
                  />
                </figure>
                <div className="mt-3 shrink-0 rounded-[14px_10px_15px_6px] border border-[var(--clay-border-soft)] bg-[var(--clay-surface)] px-3.5 py-3 shadow-[var(--clay-highlight),var(--clay-shadow-sm)]">
                  <p className="whitespace-nowrap text-[10px] font-medium uppercase tracking-[0.14em] text-[var(--accent-ink)] dark:text-[var(--accent)]">
                    Pehle, har dukaan ki kahani
                  </p>
                  <p className="mt-1.5 text-[13px] font-normal leading-relaxed text-zinc-600 dark:text-zinc-400">
                    “Maal kahan rakha tha? Kis ne udhaar liya tha? Register ka panna kahan gaya?”
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= FEATURES BENTO ================= */}
      <section id="features" className="border-y border-[var(--clay-border-soft)]">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
          <div className="max-w-2xl">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--accent-ink)]">
              Jo dukaan chalata hai
            </p>
            <h2 className="mt-3 text-2xl font-semibold leading-tight tracking-normal sm:text-3xl">
              Counter se godown tak, poora control.
            </h2>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400 sm:text-[15px]">
              Chhe modules, ek hi hisaab. Har entry traceable.
            </p>
          </div>

          <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5 lg:grid-cols-4">
            {features.map((f, i) => {
              const isPos = f.layout === 'featured';
              const layoutClass = isPos
                ? 'rounded-[28px] p-6 sm:col-span-2 sm:p-7 lg:col-span-2 lg:row-span-2'
                : f.layout === 'wide'
                  ? 'rounded-[24px] p-5 sm:col-span-2 sm:p-6 lg:col-span-2'
                  : f.layout === 'wide-end'
                    ? 'rounded-[24px] p-5 sm:p-6 lg:col-span-3'
                    : 'rounded-[20px] p-5 sm:p-6';

              return (
                <article
                  key={f.title}
                  style={{ animationDelay: `${i * 45}ms` }}
                  className={`feature-card feature-reveal flex flex-col ${layoutClass}`}
                >
                  <f.icon
                    className={`shrink-0 text-[var(--accent)] ${isPos ? 'h-6 w-6' : 'h-5 w-5'}`}
                    strokeWidth={1.75}
                  />
                  <h3 className={`mt-3 font-semibold leading-snug ${isPos ? 'text-xl sm:text-2xl' : 'text-[15px]'}`}>
                    {f.title}
                  </h3>
                  <p className="mt-1.5 max-w-md text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
                    {f.desc}
                  </p>

                  {isPos && (
                    <div className="mt-6 flex flex-1 items-end">
                      <div className="w-full rounded-[18px] border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] p-3.5 shadow-[var(--clay-highlight)]">
                        <div className="grid grid-cols-[1fr_56px_88px] items-center gap-2 border-b border-dashed border-[var(--clay-border)] pb-2">
                          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-500 dark:text-zinc-400">
                            Bill #1042
                          </span>
                          <span aria-hidden="true" />
                          <span className="text-right text-[11px] text-zinc-500 dark:text-zinc-400">Aaj</span>
                        </div>
                        <div className="grid grid-cols-[1fr_56px_88px] items-center gap-2 border-b border-[var(--clay-border-soft)] pt-2 pb-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500 dark:text-zinc-400">
                          <span>Item</span>
                          <span className="text-right">Qty</span>
                          <span className="text-right">Amount</span>
                        </div>
                        <ul className="divide-y divide-[var(--clay-border-soft)]">
                          {[
                            ['Lawn 3pc', '2.5m', '4,850'],
                            ['Cotton kurta', '1 pc', '1,650'],
                            ['Stitching', '1 pc', '800'],
                          ].map(([item, qty, amount]) => (
                            <li
                              key={item}
                              className="grid grid-cols-[1fr_56px_88px] items-center gap-2 py-2 text-[13px]"
                            >
                              <span className="min-w-0 truncate font-medium text-zinc-800 dark:text-zinc-200">
                                {item}
                              </span>
                              <span className="text-right font-tabular text-[11px] text-zinc-500 dark:text-zinc-400">
                                {qty}
                              </span>
                              <span className="text-right font-tabular font-semibold">Rs {amount}</span>
                            </li>
                          ))}
                        </ul>
                        <div className="grid grid-cols-[1fr_56px_88px] items-center gap-2 border-t border-[var(--clay-border)] pt-2.5">
                          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-500 dark:text-zinc-400">
                            Total
                          </span>
                          <span aria-hidden="true" />
                          <span className="text-right font-tabular text-sm font-semibold">Rs 7,300</span>
                        </div>
                        <div className="mt-2.5 flex items-center gap-1.5">
                          <span className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent-ink)]">
                            <Check className="h-3 w-3" strokeWidth={3} />
                            Cash
                          </span>
                          <span className="rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface)] px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                            Easypaisa
                          </span>
                        </div>
                      </div>
                    </div>
                  )}

                  {f.detail === 'flow' && (
                    <div className="mt-auto pt-3">
                      <div className="flex min-h-11 flex-wrap items-center gap-1.5 border-t border-[var(--clay-border-soft)] pt-3">
                        <span className="rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                          Lawn thaan
                        </span>
                        <span aria-hidden="true" className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          →
                        </span>
                        <span className="rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                          Cut
                        </span>
                        <span aria-hidden="true" className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          →
                        </span>
                        <span className="rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                          3pc suit
                        </span>
                      </div>
                    </div>
                  )}

                  {f.detail === 'stock' && (
                    <div className="mt-auto flex min-h-11 items-center justify-between gap-3 border-t border-[var(--clay-border-soft)] pt-3">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium">Lawn 3pc · Sky blue</p>
                        <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">Meter stock</p>
                      </div>
                      <span className="shrink-0 rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent-ink)]">
                        4m bacha
                      </span>
                    </div>
                  )}

                  {f.detail === 'khata' && (
                    <div className="mt-auto flex min-h-11 items-center justify-between gap-3 border-t border-[var(--clay-border-soft)] pt-3">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium">Ayesha Bibi</p>
                        <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">12 din se pending</p>
                      </div>
                      <span className="shrink-0 font-tabular text-xs font-semibold text-[var(--accent-ink)]">
                        Rs 6,200 pending
                      </span>
                    </div>
                  )}

                  {f.detail === 'supplier' && (
                    <div className="mt-auto flex min-h-11 items-center justify-between gap-3 border-t border-[var(--clay-border-soft)] pt-3">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium">Imran Fabrics</p>
                        <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">Is hafte dena hai</p>
                      </div>
                      <span className="shrink-0 font-tabular text-xs font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)]">
                        Rs 48,000
                      </span>
                    </div>
                  )}

                  {f.detail === 'pl' && (
                    <div className="mt-auto pt-4">
                      <div className="grid grid-cols-2 gap-x-4 border-t border-[var(--clay-border-soft)] pt-3 lg:grid-cols-4 lg:gap-x-0">
                        {[
                          { label: 'Sale', value: 'Rs 86,400', accent: false },
                          { label: 'COGS', value: 'Rs 58,250', accent: false },
                          { label: 'Kharcha', value: 'Rs 4,200', accent: false },
                          { label: 'Net profit', value: 'Rs 19,750', accent: true },
                        ].map((item, idx) => {
                          const divider =
                            idx === 0
                              ? ''
                              : idx === 1
                                ? 'border-l border-[var(--clay-border-soft)] pl-3'
                                : idx === 2
                                  ? 'border-t border-[var(--clay-border-soft)] pt-3 lg:border-l lg:border-t-0 lg:pl-3 lg:pt-0'
                                  : 'border-l border-t border-[var(--clay-border-soft)] pl-3 pt-3 lg:border-t-0 lg:pt-0';

                          return (
                            <div key={item.label} className={divider}>
                              <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500 dark:text-zinc-400">
                                {item.label}
                              </p>
                              <p
                                className={`mt-0.5 font-tabular text-sm font-semibold ${
                                  item.accent ? 'text-[var(--accent-ink)] dark:text-[var(--accent)]' : ''
                                }`}
                              >
                                {item.value}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ================= CATALOG SHOWCASE (folded fabric + variant) ================= */}
      <section className="mx-auto max-w-7xl px-4 py-14 sm:px-6 lg:px-8 lg:py-20">
        <div className="feature-reveal grid items-center gap-10 lg:grid-cols-2 lg:gap-12">
          <div className="lg:order-2">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--accent-ink)]">
              Thaan ho ya boutique piece
            </p>
            <h2 className="mt-3 text-balance text-2xl font-semibold leading-tight tracking-normal sm:text-3xl">
              Har design, har color, har meter, gin ke rakho.
            </h2>
            <p className="mt-3 max-w-[52ch] text-pretty text-sm leading-relaxed text-zinc-600 dark:text-zinc-400 sm:text-[15px]">
              Wo sky-blue wala lawn khatam hua ya nahi? KapraOS mein har color ka apna variant hai, stock khud ghat-ta
              barhta hai.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {['Open fabric', 'Ready suit', 'Boutique piece'].map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] px-3 py-1 text-xs font-normal text-zinc-700 shadow-[var(--clay-highlight),var(--clay-shadow-sm)] dark:text-zinc-300"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>

          <div className="lg:order-1">
            <div className="relative mx-auto flex w-full max-w-[26rem] flex-col lg:block lg:max-w-none">
              <div className="mx-auto w-full max-w-[26rem] overflow-hidden rounded-[28px_28px_28px_8px] border border-[var(--clay-border)] shadow-[var(--clay-highlight),var(--clay-shadow-md)] lg:mx-0 lg:w-[60%] lg:max-w-none lg:-rotate-[0.5deg]">
                <img
                  src="/pexels-arina-dmitrieva-66352626-14440420.jpg"
                  alt="Embroidered waistcoat and folded fabric stacks in a kapra shop"
                  className="aspect-[3/4] w-full object-cover object-[50%_50%] saturate-[0.88]"
                  loading="lazy"
                />
              </div>

              <div className="relative z-10 mx-auto -mt-6 w-full max-w-[24rem] rounded-[20px_20px_20px_7px] border border-[var(--clay-border-soft)] bg-[var(--clay-surface)] p-4 shadow-[var(--clay-highlight),var(--clay-shadow-md)] sm:p-5 lg:absolute lg:right-0 lg:bottom-0 lg:mt-0 lg:w-[56%] lg:max-w-none lg:rotate-[0.4deg]">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold leading-tight">Lawn, printed</p>
                  <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">Meter stock</p>
                </div>

                <ul className="mt-3 divide-y divide-[var(--clay-border-soft)] border-t border-[var(--clay-border-soft)]">
                  {[
                    { name: 'Sky Blue', stock: '4m', swatch: '#8fc7e4', low: true },
                    { name: 'Mehndi Green', stock: '22m', swatch: '#6d7c3d', low: false },
                    { name: 'Off-white', stock: '31m', swatch: '#ece3cf', low: false },
                    { name: 'Maroon', stock: '12m', swatch: '#75293a', low: false },
                  ].map((variant) => (
                    <li key={variant.name} className="flex items-center justify-between gap-3 py-2.5">
                      <span className="flex min-w-0 items-center gap-2.5">
                        <span
                          aria-hidden="true"
                          className="h-3 w-3 shrink-0 rounded-full border border-black/10 dark:border-white/20"
                          style={{ backgroundColor: variant.swatch }}
                        />
                        <span className="truncate text-[13px] text-zinc-700 dark:text-zinc-300">{variant.name}</span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        {variant.low && (
                          <span className="rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)]">
                            Kam stock
                          </span>
                        )}
                        <span
                          className={`font-tabular text-[13px] font-semibold ${
                            variant.low
                              ? 'text-[var(--accent-ink)] dark:text-[var(--accent)]'
                              : 'text-zinc-800 dark:text-zinc-100'
                          }`}
                        >
                          {variant.stock}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>

                <p className="border-t border-[var(--clay-border-soft)] pt-2.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                  Kam hone par khud alert.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= KHATA (product ledger mock) ================= */}
      <section id="khata" className="border-y border-[var(--clay-border-soft)]">
        <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6 lg:px-8 lg:py-20">
          <div className="feature-reveal grid items-center gap-10 lg:grid-cols-2 lg:gap-12">
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--accent-ink)]">
                Udhaar, ab diary mein nahi
              </p>
              <h2 className="mt-3 text-2xl font-semibold leading-tight tracking-normal sm:text-3xl">
                Har khata, apne aap update.
              </h2>
              <p className="mt-3 max-w-[46ch] text-pretty text-sm leading-relaxed text-zinc-600 dark:text-zinc-400 sm:text-[15px]">
                Udhaar sale hote hi customer ke khate mein chadh jata hai. Supplier se maal liya to payable bhi saath bante hain.
              </p>

              <div className="clay-panel-soft mt-6 grid grid-cols-2 divide-x divide-[var(--clay-border-soft)]">
                <div className="min-w-0 px-3 py-3.5 sm:px-4 sm:py-4">
                  <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                    <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300">Lena hai</span>
                    <span className="text-[10px] text-zinc-500 dark:text-zinc-400">customers</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                    <span className="font-tabular text-lg font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)] sm:text-xl">
                      Rs 21,300
                    </span>
                    <span className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400">6 udhaar</span>
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                    Ahmed Sahab ne aaj Rs 15,000 diye
                  </p>
                </div>
                <div className="min-w-0 px-3 py-3.5 sm:px-4 sm:py-4">
                  <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                    <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300">Dena hai</span>
                    <span className="text-[10px] text-zinc-500 dark:text-zinc-400">suppliers</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-baseline">
                    <span className="font-tabular text-lg font-semibold text-zinc-900 dark:text-zinc-100 sm:text-xl">
                      Rs 48,000
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                    Imran Fabrics, is hafte
                  </p>
                </div>
              </div>

              <blockquote className="mt-7 flex items-start gap-2.5">
                <BookOpenText
                  aria-hidden="true"
                  className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent-ink)] dark:text-[var(--accent)]"
                  strokeWidth={1.75}
                />
                <div className="min-w-0">
                  <p className="text-sm font-normal leading-relaxed text-zinc-800 dark:text-zinc-200">
                    “Baji, aapka pichla Rs 6,200 rehta hai. Ye suit us mein jor dun?”
                  </p>
                  <p className="mt-1.5 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
                    Khata khol ke baat karo. Customer ka bharosa, dukaan ki ronak.
                  </p>
                </div>
              </blockquote>
            </div>

            <div className="min-w-0">
              <div className="overflow-hidden clay-card">
                <div className="flex items-center justify-between gap-4 border-b border-[var(--clay-border-soft)] px-4 py-3.5 sm:px-5">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] text-sm font-semibold text-[var(--accent-ink)] shadow-[var(--clay-highlight),var(--clay-shadow-sm)] dark:text-[var(--accent)]">
                      A
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold leading-tight">Ayesha Bibi, khata</p>
                      <p className="mt-0.5 truncate text-[11px] text-zinc-500 dark:text-zinc-400">
                        Balance, history, recovery
                      </p>
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-[9px] font-medium uppercase tracking-[0.16em] text-zinc-500 dark:text-zinc-400">
                      baqaya
                    </p>
                    <p className="mt-0.5 font-tabular text-lg font-semibold leading-none">Rs 9,050</p>
                  </div>
                </div>

                <div className="divide-y divide-[var(--clay-border-soft)] px-4 sm:px-5">
                  {[
                    { date: '12 Feb', label: 'Lawn 3pc, udhaar', amount: 'Rs 6,200', balance: '6,200', positive: false },
                    { date: '18 Feb', label: 'Payment received', amount: '- Rs 2,000', balance: '4,200', positive: true },
                    { date: '24 Feb', label: 'Embroidered kurta, udhaar', amount: 'Rs 4,850', balance: '9,050', positive: false },
                  ].map((row) => (
                    <div
                      key={row.date}
                      className="grid grid-cols-[3.25rem_minmax(0,1fr)_auto] items-center gap-3 py-3"
                    >
                      <span className="font-tabular text-[11px] text-zinc-500 dark:text-zinc-400">{row.date}</span>
                      <p className="min-w-0 text-[13px] leading-snug text-zinc-700 dark:text-zinc-300">{row.label}</p>
                      <div className="text-right">
                        <p
                          className={`font-tabular text-sm font-semibold ${
                            row.positive
                              ? 'text-emerald-700 dark:text-emerald-400'
                              : 'text-zinc-900 dark:text-zinc-100'
                          }`}
                        >
                          {row.amount}
                        </p>
                        <p className="mt-0.5 font-tabular text-[10px] text-zinc-500 dark:text-zinc-400">
                          baqaya {row.balance}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="flex flex-col gap-3 border-t border-[var(--clay-border-soft)] px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                  <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                    Auto-posted from POS. No diary needed.
                  </p>
                  <span className="clay-btn clay-btn-accent inline-flex shrink-0 items-center justify-center rounded-xl bg-[var(--accent)] px-4 py-2 text-xs font-semibold text-[var(--accent-on)]">
                    Recover Rs 2,000
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= HOW IT WORKS ================= */}
      <section id="how" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="mx-auto max-w-2xl text-center">
          <SectionEyebrow>Subah se shaam tak</SectionEyebrow>
          <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Dukaan ka routine, KapraOS ke saath</h2>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {steps.map((s) => (
            <div key={s.n} className="clay-card relative overflow-hidden p-6">
              <span className="font-tabular text-5xl font-extrabold text-zinc-200 dark:text-zinc-800">{s.n}</span>
              <h3 className="mt-2 text-base font-bold">{s.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">{s.desc}</p>
            </div>
          ))}
        </div>

        {/* AI band */}
        <div id="ai" className="relative mt-10 overflow-hidden rounded-3xl border border-zinc-800 bg-[#1e222b] text-white dark:border-zinc-700">
          <img
            src="/pexels-teona-swift-6850486.jpg"
            alt="Colorful fabric texture close-up"
            className="absolute inset-0 h-full w-full object-cover opacity-35"
            loading="lazy"
          />
          <div className="absolute inset-0 bg-linear-to-r from-[#1e222b] via-[#1e222b]/70 to-[#1e222b]/40" />
          <div className="relative grid gap-8 p-8 sm:p-10 lg:grid-cols-[1.1fr_0.9fr] lg:p-12">
            <div>
              <p className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 text-xs font-semibold">
                <Sparkles className="h-3.5 w-3.5 text-blue-300" /> AI Munshi — live on your data
              </p>
              <h3 className="mt-4 text-2xl font-extrabold tracking-tight sm:text-3xl">Bas poocho: “Aaj kitni sale hui?”</h3>
              <p className="mt-3 max-w-lg text-sm leading-relaxed text-zinc-300">
                KapraOS ka shop-assistant aapke asal stock, sale aur khate par jawab deta hai —
                low-stock warning, udhaar reminder, profit summary. Jaise purana munshi, par jo sota nahi.
              </p>
              <div className="mt-5 flex flex-wrap gap-2 text-xs">
                {['“Lawn mein kya khatam ho raha hai?”', '“Ahmed ka udhaar kitna hai?”', '“Is hafte ka profit batao”'].map((q) => (
                  <span key={q} className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 font-medium text-zinc-200">
                    <MessageCircleQuestion className="h-3.5 w-3.5 text-blue-300" /> {q}
                  </span>
                ))}
              </div>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/6 p-4 backdrop-blur">
              <div className="space-y-3 text-sm">
                <div className="ml-auto w-fit max-w-[85%] rounded-xl rounded-br-sm bg-white px-3.5 py-2.5 font-medium text-zinc-900">
                  Aaj kitni sale hui?
                </div>
                <div className="w-fit max-w-[90%] rounded-xl rounded-bl-sm border border-white/10 bg-zinc-900 px-3.5 py-2.5 text-zinc-100">
                  Aaj <strong className="font-tabular">Rs 86,400</strong> ki 23 sales hui — 6 udhaar (Rs 21,300). Net profit <strong className="font-tabular text-emerald-300">Rs 19,750</strong>. 
                </div>
                <div className="ml-auto w-fit max-w-[85%] rounded-xl rounded-br-sm bg-white px-3.5 py-2.5 font-medium text-zinc-900">
                  Aur kya khatam ho raha hai?
                </div>
                <div className="w-fit max-w-[90%] rounded-xl rounded-bl-sm border border-white/10 bg-zinc-900 px-3.5 py-2.5 text-zinc-100">
                  Lawn Sky Blue — sirf 4m bacha hai. Dobara order kar lo?
                </div>
              </div>
              <button
                onClick={() => navigate(isAuthenticated ? '/ai-chat' : '/signup')}
                className="mt-4 inline-flex h-10 w-full cursor-pointer items-center justify-center gap-2 clay-btn rounded-xl bg-linear-to-r from-indigo-600 to-blue-500 text-sm font-bold text-white shadow-lg shadow-indigo-900/20 transition-all hover:-translate-y-0.5 hover:from-indigo-500 hover:to-blue-400"
              >
                <Sparkles className="h-4 w-4" />
                {isAuthenticated ? 'AI Munshi se baat karo' : 'AI Munshi try karo — free'}
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ================= FINAL CTA ================= */}
      <section className="mx-auto max-w-7xl px-4 pb-16 sm:px-6 lg:px-8 lg:pb-24">
        <div className="relative overflow-hidden rounded-3xl border border-zinc-200 bg-[#1e222b] text-white shadow-2xl dark:border-zinc-700">
          <div aria-hidden className="pointer-events-none absolute inset-0">
            <div className="absolute -top-24 left-1/4 h-72 w-72 rounded-full bg-indigo-500/25 blur-3xl" />
            <div className="absolute -bottom-24 right-1/4 h-72 w-72 rounded-full bg-blue-500/20 blur-3xl" />
          </div>
          <div className="relative px-6 py-14 text-center sm:px-12 lg:py-18">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-400">Apni bahi ko digital karo</p>
            <h2 className="mx-auto mt-3 max-w-2xl text-3xl font-extrabold tracking-tight sm:text-4xl">
              {isAuthenticated ? 'Dashboard tayyar hai — dukaan kholo.' : 'Aaj hi apni dukaan KapraOS par lao.'}
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-zinc-300 sm:text-base">
              Stock, sale, khata aur profit — sab ek jagah. Chahe ek counter ho ya teen godown.
            </p>
            <div className="mt-7 flex flex-col items-center justify-center gap-3">
              <button
                onClick={primaryCta}
                className="inline-flex h-12 cursor-pointer items-center gap-2 clay-btn rounded-xl bg-linear-to-r from-indigo-600 to-blue-500 px-7 text-[15px] font-bold text-white shadow-lg shadow-indigo-900/30 transition-all hover:-translate-y-0.5 hover:from-indigo-500 hover:to-blue-400"
              >
                {isAuthenticated ? 'Open Dashboard' : 'Create free account'}
                <ArrowRight className="h-4.5 w-4.5" />
              </button>
              {!isAuthenticated && (
                <Link
                  to="/login"
                  className="text-sm font-medium text-zinc-300 underline-offset-4 hover:text-white hover:underline"
                >
                  I already have an account
                </Link>
              )}
            </div>
            <p className="mt-5 text-xs text-zinc-400">No credit card • Urdu-friendly • Mobile par bhi chalega</p>
          </div>
        </div>
      </section>

      {/* ================= FOOTER ================= */}
      <footer className="border-t border-[var(--clay-border-soft)] bg-[var(--clay-surface)]">
        <div className="mx-auto grid max-w-7xl gap-8 px-4 py-12 sm:px-6 md:grid-cols-[1.2fr_1fr_1fr_1fr] lg:px-8">
          <div>
            <div className="flex items-center gap-2.5">
              <span className="clay-icon-tile h-9 w-9 bg-[var(--clay-primary)] text-white border-transparent">
                <Store className="h-4.5 w-4.5" />
              </span>
              <span className="text-[15px] font-bold">KapraOS</span>
            </div>
            <p className="mt-3 max-w-xs text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
              Fabric & Fashion Retail Management — POS, inventory, khata, accounting aur AI. Apni bahi ko digital karo.
            </p>
            <div className="mt-4 flex gap-2">
              <Users_Widget />
            </div>
          </div>
          <FooterCol
            title="Dukaan"
            links={[
              ['Features', '#features'],
              ['Khata system', '#khata'],
              ['Dukaan flow', '#how'],
              ['AI Munshi', '#ai'],
            ]}
          />
          <FooterCol
            title="App"
            links={[
              [isAuthenticated ? 'Open Dashboard' : 'Sign in', isAuthenticated ? '/dashboard' : '/login'],
              [isAuthenticated ? 'AI Chat' : 'Create account', isAuthenticated ? '/ai-chat' : '/signup'],
              ['Daily Reports', '/reports'],
              ['Analytics', '/analytics'],
            ]}
          />
          <FooterCol
            title="Hisaab"
            links={[
              ['Products & Variants', '/products'],
              ['Inventory', '/inventory'],
              ['New Sale (POS)', '/sales/new'],
              ['Expenses', '/expenses'],
            ]}
          />
        </div>
        <div className="border-t border-zinc-200 dark:border-zinc-800">
          <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-2 px-4 py-5 text-xs text-zinc-500 sm:flex-row sm:px-6 lg:px-8 dark:text-zinc-400">
            <p>© {new Date().getFullYear()} KapraOS — Retail Management System. All rights reserved.</p>
            <p className="inline-flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
              <ShieldCheck className="h-3.5 w-3.5" /> Secure • Ledger-balanced • Shop-first • No register needed • Meters, yards & pieces • Easypaisa / JazzCash ready
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}

function FooterCol({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <div>
      <p className="text-xs font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">{title}</p>
      <ul className="mt-3 space-y-2">
        {links.map(([label, href]) => (
          <li key={label + href}>
            {href.startsWith('#') ? (
              <a href={href} className="text-sm text-zinc-600 transition-colors hover:text-indigo-700 dark:text-zinc-400 dark:hover:text-indigo-300">
                {label}
              </a>
            ) : (
              <Link to={href} className="text-sm text-zinc-600 transition-colors hover:text-indigo-700 dark:text-zinc-400 dark:hover:text-indigo-300">
                {label}
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Users_Widget() {
  return (
    <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
      <span className="flex -space-x-2">
        {['AB', 'IK', 'SR'].map((t) => (
          <span
            key={t}
            className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-white bg-zinc-800 text-[10px] font-bold text-white dark:border-zinc-950 dark:bg-zinc-700"
          >
            {t}
          </span>
        ))}
      </span>
      <span>Dukandaaron ka bharosa — Lahore • Karachi • Faisalabad</span>
    </div>
  );
}

export default LandingPage;
