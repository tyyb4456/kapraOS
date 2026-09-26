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
          className={`inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-md bg-linear-to-r from-rose-600 to-orange-500 px-4 text-sm font-medium text-white shadow-sm shadow-rose-900/20 transition-all hover:from-rose-700 hover:to-orange-600 ${mobile ? 'w-full' : ''}`}
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
        className={`inline-flex h-9 items-center justify-center rounded-md border border-zinc-300 bg-white px-4 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-transparent dark:text-zinc-200 dark:hover:bg-zinc-800 ${mobile ? 'w-full' : ''}`}
      >
        Sign in
      </Link>
      <Link
        to="/signup"
        className={`inline-flex h-9 items-center justify-center gap-1.5 rounded-md bg-linear-to-r from-rose-600 to-orange-500 px-4 text-sm font-medium text-white shadow-sm shadow-rose-900/20 transition-all hover:from-rose-700 hover:to-orange-600 ${mobile ? 'w-full' : ''}`}
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
    <p className="mb-3 inline-flex items-center gap-2 rounded-full border border-zinc-200 bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-widest text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
      {children}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Data                                                                */
/* ------------------------------------------------------------------ */

/* Single accent system: pink → orange gradient for CTAs / highlights.
   Feature icons use ONE tone (accent tint). Neutral zinc is the second tone.
   Green is reserved for success states only (recovery / profit). */
const FEATURE_ICON_STYLE =
  'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:border-rose-500/30';

const features = [
  {
    icon: Receipt,
    title: 'POS built for kapra',
    desc: 'Meter-cut or ready-piece billing in seconds. Cash, card, bank, Easypaisa, JazzCash — plus udhaar in one tap.',
  },
  {
    icon: Scissors,
    title: 'Open fabric → ready suits',
    desc: 'Category → Product → Variant model handles thaans, 3-piece suits and boutique pieces with meters, yards & pieces.',
  },
  {
    icon: Package,
    title: 'Inventory that warns you',
    desc: 'Live stock by variant, low-stock alerts and every meter movement tracked — sale, purchase, adjustment.',
  },
  {
    icon: BookOpenText,
    title: 'Customer Khata, zero diary',
    desc: 'Every udhaar auto-posted to the ledger. Balances, recoveries and khata history per customer — no more lost pages.',
  },
  {
    icon: Truck,
    title: 'Supplier Khata & purchases',
    desc: 'Purchase bills, supplier credit and payables in one flow. Know exactly what you owe, to whom, since when.',
  },
  {
    icon: BarChart3,
    title: 'Real hisaab, not guesswork',
    desc: 'Double-entry ledger powers P&L, trial balance, daily sales, COGS and net profit. Evening closing in one glance.',
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
    <div className="min-h-screen bg-[#f8f9fa] text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
      {/* ================= NAVBAR ================= */}
      <header className="sticky top-0 z-50 border-b border-zinc-200/80 bg-white/85 backdrop-blur-md dark:border-zinc-800 dark:bg-zinc-950/85">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900">
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
              <a key={href} href={href} className="rounded-md px-3 py-2 transition-colors hover:bg-rose-50 hover:text-rose-700 dark:hover:bg-rose-500/10 dark:hover:text-rose-300">
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
          <div className="border-t border-zinc-200 bg-white px-4 py-4 md:hidden dark:border-zinc-800 dark:bg-zinc-950">
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
      <section className="relative overflow-hidden">
        {/* soft accent glow — single pink→orange family */}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          <div className="absolute -top-32 right-[-10%] h-105 w-130 rounded-full bg-rose-200/40 blur-3xl dark:bg-rose-500/10" />
          <div className="absolute top-40 left-[-12%] h-95 w-115 rounded-full bg-orange-200/40 blur-3xl dark:bg-orange-500/10" />
        </div>

        <div className="relative mx-auto grid max-w-7xl items-center gap-12 px-4 pt-14 pb-16 sm:px-6 lg:grid-cols-[1.05fr_0.95fr] lg:gap-10 lg:px-8 lg:pt-20 lg:pb-24">
          {/* copy — 1 badge, headline, subtext, 1 primary CTA */}
          <div>
    

            <h1 className="text-4xl font-extrabold leading-[1.05] tracking-tight sm:text-5xl lg:text-[3.6rem]">
              Aapki poori kapra dukaan,
              <span className="block bg-linear-to-r from-rose-600 to-orange-500 bg-clip-text text-transparent dark:from-rose-400 dark:to-orange-300">
                ab ek hi screen par.
              </span>
            </h1>

            <p className="mt-5 max-w-xl text-base leading-relaxed text-zinc-600 sm:text-lg dark:text-zinc-400">
              Thaan ho ya 3-piece, udhaar ho ya cash — <strong className="font-semibold text-zinc-900 dark:text-zinc-100">KapraOS</strong> stock,
              sale, customer khata, supplier hisaab aur daily profit ko ek hi jagah jor deta hai. Register band, tension khatam.
            </p>

            <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:items-center">
              <button
                onClick={primaryCta}
                disabled={isLoading}
                className="inline-flex h-12 cursor-pointer items-center justify-center gap-2 rounded-lg bg-linear-to-r from-rose-600 to-orange-500 px-6 text-[15px] font-semibold text-white shadow-lg shadow-rose-900/20 transition-all hover:-translate-y-0.5 hover:from-rose-700 hover:to-orange-600 disabled:opacity-60"
              >
                {isLoading ? 'Loading…' : isAuthenticated ? 'Open Dashboard' : 'Start free — Apni dukaan joro'}
                <ArrowRight className="h-4.5 w-4.5" />
              </button>
            </div>
            {!isLoading && (
              <div className="mt-3">
                {!isAuthenticated ? (
                  <Link
                    to="/login"
                    className="text-sm font-medium text-rose-700 underline-offset-4 hover:text-rose-800 hover:underline dark:text-rose-300 dark:hover:text-rose-200"
                  >
                    Already have an account? Sign in →
                  </Link>
                ) : (
                  <a
                    href="#features"
                    className="text-sm font-medium text-rose-700 underline-offset-4 hover:text-rose-800 hover:underline dark:text-rose-300 dark:hover:text-rose-200"
                  >
                    Explore features ↓
                  </a>
                )}
              </div>
            )}

            {/* mini stats — kept; checkmarks removed to avoid duplication */}
            <dl className="mt-8 grid max-w-lg grid-cols-3 gap-3">
              {[
                ['POS', '30-sec billing'],
                ['Khata', 'Auto udhaar'],
                ['P&L', 'Daily profit'],
              ].map(([k, v]) => (
                <div key={k} className="rounded-xl border border-zinc-200 bg-white/80 p-3.5 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/80">
                  <dt className="text-lg font-extrabold tracking-tight">{k}</dt>
                  <dd className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{v}</dd>
                </div>
              ))}
            </dl>
          </div>

          {/* visual — boutique hero pic, single overlay (price tag as product proof) */}
          <div className="relative">
            <div className="relative overflow-hidden rounded-3xl border border-zinc-200 shadow-2xl shadow-zinc-900/15 dark:border-zinc-800">
              <img
                src="/pexels-arina-dmitrieva-66352626-14440412.jpg"
                alt="Boutique suit and woven shawl hanging in a fabric shop — managed in KapraOS"
                className="h-105 w-full object-cover sm:h-130 lg:h-150"
                loading="eager"
              />
              <div className="absolute inset-0 bg-linear-to-t from-zinc-950/55 via-transparent to-transparent" />
              <div className="absolute bottom-4 left-4 right-4 flex items-end justify-between gap-3">
                <div className="rounded-xl bg-white/95 px-4 py-3 shadow-lg backdrop-blur dark:bg-zinc-900/95">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">Ready suit • Boutique</p>
                  <p className="text-sm font-bold">Embroidered kurta — Variant tracked</p>
                </div>
                <div className="hidden rounded-xl bg-zinc-900/90 px-4 py-3 text-white shadow-lg backdrop-blur sm:block dark:bg-white/95 dark:text-zinc-900">
                  <p className="font-tabular text-lg font-extrabold leading-none">Rs 4,850</p>
                  <p className="mt-1 text-[11px] opacity-80">POS billed in 30 sec</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= PAIN → RELIEF (overwhelmed pic) ================= */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div className="order-2 lg:order-1">
            {/* Product mock — evening closing dashboard (replaces distressed-people photo) */}
            <div className="overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3.5 dark:border-zinc-800">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">Aaj ka hisaab — Evening closing</p>
                  <p className="text-sm font-bold">Dashboard • Live ledger</p>
                </div>
                <span className="rounded-full bg-linear-to-r from-rose-600 to-orange-500 px-3 py-1 text-[11px] font-bold text-white">Live</span>
              </div>
              <div className="space-y-3 p-5">
                {[
                  ['Aaj ki sale — 23 bills', 'Rs 86,400', false],
                  ['Udhaar recovery', '+ Rs 15,000', true],
                  ['Kharcha', 'Rs 4,200', false],
                  ['Low-stock variants', '3 alerts', false],
                ].map(([label, val, positive]) => (
                  <div key={label as string} className="flex items-center justify-between rounded-xl border border-zinc-200 bg-[#f8f9fa] px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900">
                    <p className="text-sm font-medium">{label}</p>
                    <p className={`font-tabular text-sm font-extrabold ${positive ? 'text-emerald-600 dark:text-emerald-400' : ''}`}>{val}</p>
                  </div>
                ))}
                <div className="flex items-center justify-between rounded-xl bg-zinc-900 px-4 py-3.5 text-white dark:bg-zinc-100 dark:text-zinc-900">
                  <p className="text-sm font-semibold">Net profit — aaj</p>
                  <p className="font-tabular text-base font-extrabold text-emerald-400 dark:text-emerald-600">Rs 19,750</p>
                </div>
              </div>
            </div>
            <div className="relative mt-4 overflow-hidden rounded-2xl border border-zinc-200 shadow-lg dark:border-zinc-800">
              <img
                src="/pexels-ron-lach-8453642.jpg"
                alt="Kapron ke dher mein dukandaar — pehle ka haal"
                className="h-56 w-full object-cover sm:h-64"
                loading="lazy"
              />
              <div className="absolute inset-0 bg-linear-to-t from-zinc-950/75 via-zinc-950/15 to-transparent" />
              <div className="absolute bottom-3 left-3 right-3">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-orange-200">Pehle — har dukaan ki kahani</p>
                <p className="mt-1 text-sm font-medium leading-relaxed text-white">“Maal kahan rakha tha? Kis ne udhaar liya tha? Register ka panna kahan gaya?”</p>
              </div>
            </div>
          </div>
          <div className="order-1 lg:order-2">
            <SectionEyebrow>Dher se — System tak</SectionEyebrow>
            <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
              Kapron ke dher mein{' '}
              <span className="bg-linear-to-r from-rose-600 to-orange-500 bg-clip-text text-transparent dark:from-rose-400 dark:to-orange-300">hisaab</span>{' '}
              nahi dhoondna parta.
            </h2>
            <p className="mt-4 leading-relaxed text-zinc-600 dark:text-zinc-400">
              Roz shaam ko copy mein sale jorna, udhaar yaad rakhna, aur godown mein thaans ginna — yehi dukandaar ki thakaan hai.
              KapraOS ye teenon kaam khud karta hai: <strong className="text-zinc-900 dark:text-zinc-100">bill bante hi stock ghat-ta hai, udhaar khud khate mein likha jata hai, profit khud nikal ata hai.</strong>
            </p>
            <ul className="mt-6 space-y-3">
              {[
                'Har variant ka live stock — color, fabric aur size ke saath',
                'Har udhaar customer ke khate mein — recovery ke saath',
                'Har din ka P&L — sale, COGS, kharcha, net profit',
              ].map((li) => (
                <li key={li} className="flex items-start gap-2.5 text-sm">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </span>
                  <span className="font-medium">{li}</span>
                </li>
              ))}
            </ul>
            <button
              onClick={primaryCta}
              className="mt-7 inline-flex h-11 cursor-pointer items-center gap-2 rounded-lg bg-linear-to-r from-rose-600 to-orange-500 px-5 text-sm font-semibold text-white shadow-lg shadow-rose-900/20 transition-all hover:-translate-y-0.5 hover:from-rose-700 hover:to-orange-600"
            >
              {isAuthenticated ? 'Go to Dashboard' : 'Dher khatam karo — Start free'}
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </section>

      {/* ================= FEATURES GRID ================= */}
      <section id="features" className="border-y border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900/40">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
          <div className="mx-auto max-w-2xl text-center">
            <SectionEyebrow>Jo dukaan chalata hai — sab kuch</SectionEyebrow>
            <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Counter se godown tak, poora control</h2>
            <p className="mt-3 text-zinc-600 dark:text-zinc-400">Six modules, one ledger. Har entry double-checked, har rupaya traceable.</p>
          </div>

          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {features.map((f) => (
              <div
                key={f.title}
                className="group rounded-2xl border border-zinc-200 bg-[#f8f9fa] p-6 transition-all hover:-translate-y-1 hover:shadow-xl hover:shadow-zinc-900/10 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:shadow-black/40"
              >
                <span className={`inline-flex h-11 w-11 items-center justify-center rounded-xl border ${FEATURE_ICON_STYLE}`}>
                  <f.icon className="h-5 w-5" />
                </span>
                <h3 className="mt-4 text-[15px] font-bold">{f.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ================= CATALOG SHOWCASE (embroidered pic) ================= */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="grid items-center gap-10 lg:grid-cols-[0.95fr_1.05fr]">
          <div className="relative">
            <div className="grid grid-cols-2 gap-3">
              <div className="overflow-hidden rounded-2xl border border-zinc-200 shadow-lg dark:border-zinc-800">
                <img
                  src="/pexels-arina-dmitrieva-66352626-14440420.jpg"
                  alt="Embroidered waistcoat with woven shawl and folded colorful thaans in a fabric store"
                  className="h-80 w-full object-cover sm:h-125"
                  loading="lazy"
                />
              </div>
              <div className="overflow-hidden rounded-2xl border border-zinc-200 shadow-lg dark:border-zinc-800">
                <img
                  src="/pexels-irrabagon-37507373.jpg"
                  alt="Printed lawn fabric hanging — color and print variants"
                  className="h-80 w-full object-cover sm:h-125"
                  loading="lazy"
                />
              </div>
            </div>
            <div className="mt-3 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <p className="text-xs font-semibold uppercase tracking-widest text-rose-700 dark:text-rose-300">Print • Color • Meter</p>
              <p className="mt-1 text-sm font-medium leading-relaxed">Har print apna variant — sale par ghat-ta, purchase par barhta.</p>
            </div>
          </div>
          <div>
            <SectionEyebrow>Thaan ho ya boutique piece</SectionEyebrow>
            <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Har design, har color, har meter — gin ke rakho</h2>
            <p className="mt-4 leading-relaxed text-zinc-600 dark:text-zinc-400">
              Kapra ki dukaan ka sab se bara dard: <em>“wo sky-blue wala lawn khatam hua ya nahi?”</em> KapraOS mein har fabric,
              color aur dupatta-combo apna <strong className="text-zinc-900 dark:text-zinc-100">variant</strong> hai — purchase par stock barhta hai,
              sale par ghat-ta hai, kam ho to alert bajta hai.
            </p>
            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              {[
                ['OPEN_FABRIC', 'Gazz / meter ke thaans — katayi stock'],
                ['READY_SUIT', '3-piece / 2-piece stitched stock'],
                ['BOUTIQUE', 'Single designer piece tracking'],
                ['Low-stock bell', 'Khatam hone se pehle khabar'],
              ].map(([t, d]) => (
                <div key={t} className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                  <p className="text-xs font-extrabold uppercase tracking-wider text-zinc-900 dark:text-zinc-100">{t}</p>
                  <p className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400">{d}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ================= KHATA (product ledger mock) ================= */}
      <section id="khata" className="border-y border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900/40">
        <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
          <div className="grid items-center gap-10 lg:grid-cols-2">
            <div>
              <SectionEyebrow>Udhaar — ab diary mein nahi</SectionEyebrow>
              <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Customer khata + Supplier khata, auto-updated</h2>
              <p className="mt-4 leading-relaxed text-zinc-600 dark:text-zinc-400">
                Udhaar sale hote hi <strong className="text-zinc-900 dark:text-zinc-100">receivable</strong> ban jata hai, payment ate hi clear.
                Supplier se maal udhaar liya to <strong className="text-zinc-900 dark:text-zinc-100">payable</strong> ready.
                Kaun kitna dega, kab se pending hai — sab khate mein, tareekh ke saath.
              </p>
              <div className="mt-6 space-y-3">
                {[
                  ['Ayesha Bibi — Lawn 3pc (udhaar)', 'Rs 6,200', '12 din se pending', false],
                  ['Imran Fabrics — Supplier payable', 'Rs 48,000', 'Is hafte dena hai', false],
                  ['Recovery — Ahmed Sahab', '+ Rs 15,000', 'Aaj received ✓', true],
                ].map(([name, amt, sub, positive]) => (
                  <div key={name as string} className="flex items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-[#f8f9fa] px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900">
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-zinc-900 text-xs font-bold text-white dark:bg-zinc-100 dark:text-zinc-900">
                        {(name as string).charAt(0)}
                      </span>
                      <div>
                        <p className="text-sm font-bold leading-tight">{name}</p>
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">{sub}</p>
                      </div>
                    </div>
                    <p className={`font-tabular text-sm font-extrabold ${positive ? 'text-emerald-600 dark:text-emerald-400' : ''}`}>{amt}</p>
                  </div>
                ))}
              </div>
              <blockquote className="mt-6 rounded-r-2xl border-l-4 border-rose-500 bg-[#f8f9fa] px-4 py-3 dark:bg-zinc-900">
                <div className="flex items-center gap-2">
                  <BookOpenText className="h-4 w-4 shrink-0 text-rose-700 dark:text-rose-300" />
                  <p className="text-sm font-bold">“Baji, aapka pichla Rs 6,200 rehta hai — ye suit us mein jor dun?”</p>
                </div>
                <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">Khata khol ke baat karo — customer ka bharosa, dukaan ki ronak.</p>
              </blockquote>
            </div>
            <div>
              {/* Product mock — customer khata ledger (replaces laundry photo) */}
              <div className="overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
                <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3.5 dark:border-zinc-800">
                  <div className="flex items-center gap-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-full bg-linear-to-br from-rose-600 to-orange-500 text-xs font-bold text-white">A</span>
                    <div>
                      <p className="text-sm font-bold leading-tight">Ayesha Bibi — Khata</p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400">Balance • History • Recovery</p>
                    </div>
                  </div>
                  <p className="font-tabular text-sm font-extrabold">Rs 6,200</p>
                </div>
                <div className="space-y-3 p-5">
                  {[
                    ['Lawn 3pc — Udhaar sale', '12 Feb', 'Rs 6,200'],
                    ['Payment received', '18 Feb', '− Rs 2,000'],
                    ['Embroidered kurta — Udhaar', '24 Feb', 'Rs 4,850'],
                  ].map(([label, date, val]) => (
                    <div key={label as string} className="flex items-center justify-between rounded-xl border border-zinc-200 bg-[#f8f9fa] px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900">
                      <div>
                        <p className="text-sm font-semibold leading-tight">{label}</p>
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">{date}</p>
                      </div>
                      <p className="font-tabular text-sm font-bold">{val}</p>
                    </div>
                  ))}
                  <div className="flex items-center justify-between gap-3 pt-1">
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">Auto-posted from POS • No diary needed</p>
                    <span className="rounded-lg bg-linear-to-r from-rose-600 to-orange-500 px-4 py-2 text-xs font-bold text-white">Recover Rs 2,000</span>
                  </div>
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
            <div key={s.n} className="relative overflow-hidden rounded-2xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
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
                <Sparkles className="h-3.5 w-3.5 text-orange-300" /> AI Munshi — live on your data
              </p>
              <h3 className="mt-4 text-2xl font-extrabold tracking-tight sm:text-3xl">Bas poocho: “Aaj kitni sale hui?”</h3>
              <p className="mt-3 max-w-lg text-sm leading-relaxed text-zinc-300">
                KapraOS ka shop-assistant aapke asal stock, sale aur khate par jawab deta hai —
                low-stock warning, udhaar reminder, profit summary. Jaise purana munshi, par jo sota nahi.
              </p>
              <div className="mt-5 flex flex-wrap gap-2 text-xs">
                {['“Lawn mein kya khatam ho raha hai?”', '“Ahmed ka udhaar kitna hai?”', '“Is hafte ka profit batao”'].map((q) => (
                  <span key={q} className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 font-medium text-zinc-200">
                    <MessageCircleQuestion className="h-3.5 w-3.5 text-orange-300" /> {q}
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
                className="mt-4 inline-flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-lg bg-linear-to-r from-rose-600 to-orange-500 text-sm font-bold text-white shadow-lg shadow-rose-900/20 transition-all hover:-translate-y-0.5 hover:from-rose-500 hover:to-orange-400"
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
            <div className="absolute -top-24 left-1/4 h-72 w-72 rounded-full bg-rose-500/25 blur-3xl" />
            <div className="absolute -bottom-24 right-1/4 h-72 w-72 rounded-full bg-orange-500/20 blur-3xl" />
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
                className="inline-flex h-12 cursor-pointer items-center gap-2 rounded-lg bg-linear-to-r from-rose-600 to-orange-500 px-7 text-[15px] font-bold text-white shadow-lg shadow-rose-900/30 transition-all hover:-translate-y-0.5 hover:from-rose-500 hover:to-orange-400"
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
      <footer className="border-t border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
        <div className="mx-auto grid max-w-7xl gap-8 px-4 py-12 sm:px-6 md:grid-cols-[1.2fr_1fr_1fr_1fr] lg:px-8">
          <div>
            <div className="flex items-center gap-2.5">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900">
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
              <a href={href} className="text-sm text-zinc-600 transition-colors hover:text-rose-700 dark:text-zinc-400 dark:hover:text-rose-300">
                {label}
              </a>
            ) : (
              <Link to={href} className="text-sm text-zinc-600 transition-colors hover:text-rose-700 dark:text-zinc-400 dark:hover:text-rose-300">
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
