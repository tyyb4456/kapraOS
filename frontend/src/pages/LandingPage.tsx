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
  Check,
  Menu,
  X,
  Receipt,
  Send,
  LayoutDashboard,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext.tsx';
import { ThemeToggle } from '../components/ui/ThemeToggle.tsx';
import {
  Chip,
  ClayCard,
  Eyebrow,
  SectionCopy,
  SectionHeading,
} from '../components/landing/LandingPrimitives.tsx';

const navItems = [
  { label: 'Features', href: '#features' },
  { label: 'Khata', href: '#khata' },
  { label: 'Dukaan flow', href: '#how' },
  { label: 'AI Munshi', href: '#ai' },
];

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

function AuthButtons({ mobile = false }: { mobile?: boolean }) {
  const { isAuthenticated, isLoading, user } = useAuth();
  const navigate = useNavigate();

  if (isLoading) {
    return (
      <div className={`flex ${mobile ? 'w-full flex-col' : 'items-center'} gap-2`}>
        <div className="h-9 w-24 animate-pulse rounded-xl bg-zinc-200 dark:bg-zinc-800" />
        <div className="h-9 w-32 animate-pulse rounded-xl bg-zinc-200 dark:bg-zinc-800" />
      </div>
    );
  }

  if (isAuthenticated) {
    return (
      <div className={`flex ${mobile ? 'w-full flex-col' : 'items-center'} gap-2`}>
        {!mobile && user?.fullName && (
          <span className="hidden max-w-40 truncate text-xs text-zinc-500 lg:block dark:text-zinc-400">
            Salaam, <span className="font-semibold text-zinc-800 dark:text-zinc-200">{user.fullName.split(' ')[0]}</span>
          </span>
        )}
        <button
          onClick={() => navigate('/dashboard')}
          className={`clay-btn landing-btn landing-btn-secondary inline-flex h-9 cursor-pointer items-center justify-center gap-2 px-4 text-sm ${
            mobile ? 'w-full' : ''
          }`}
        >
          <LayoutDashboard className="h-4 w-4" />
          Open Dashboard
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className={`flex ${mobile ? 'w-full flex-col' : 'items-center'} gap-2`}>
      <Link
        to="/login"
        className={`landing-text-link inline-flex h-9 items-center justify-center px-3 ${mobile ? 'w-full' : ''}`}
      >
        Sign in
      </Link>
      <Link
        to="/signup"
        className={`clay-btn landing-btn landing-btn-secondary inline-flex h-9 items-center justify-center gap-1.5 px-4 text-sm ${
          mobile ? 'w-full' : ''
        }`}
      >
        Create free account
        <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
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
    time: 'Subah',
    title: 'Dashboard kholo',
    desc: 'Aaj ki sale, kharcha, udhaar recovery aur low-stock ek screen par.',
  },
  {
    time: 'Din',
    title: 'Sale aur purchase',
    desc: 'New Sale par meter kaat ke bill. Purchase par stock khud barhta hai.',
  },
  {
    time: 'Shaam',
    title: 'Reports band karo',
    desc: 'Daily sales, P&L, aur AI se poocho: aaj kitni sale hui?',
  },
];

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export function LandingPage() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();

  const primaryCta = () => {
    if (isLoading) return;
    navigate(isAuthenticated ? '/dashboard' : '/signup');
  };
  const openAi = () => {
    if (isLoading) return;
    navigate(isAuthenticated ? '/ai-chat' : '/signup');
  };

  return (
    <div className="landing-page min-h-screen bg-[var(--clay-bg)] text-zinc-900 antialiased dark:text-zinc-100">
      {/* ================= NAVBAR ================= */}
      <header className="landing-header sticky top-0 z-50">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="clay-icon-tile h-9 w-9 border-transparent bg-[var(--accent)] text-[var(--accent-on)]">
              <Store className="h-4.5 w-4.5" />
            </span>
            <span className="leading-tight">
              <span className="block text-[15px] font-bold tracking-tight">KapraOS</span>
              <span className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                Apni bahi ko digital karo
              </span>
            </span>
          </Link>

          <nav aria-label="Primary" className="hidden items-center gap-1 text-sm font-medium md:flex">
            {navItems.map(({ label, href }) => (
              <a key={href} href={href} className="landing-nav-link">
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
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              className="flex h-9 w-9 items-center justify-center rounded-xl text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>

        {menuOpen && (
          <div
            id="mobile-menu"
            className="border-t border-[var(--clay-border-soft)] bg-[var(--clay-surface)] px-4 py-4 md:hidden"
          >
            <nav aria-label="Mobile" className="mb-4 flex flex-col gap-1 text-sm font-medium">
              {navItems.map(({ label, href }) => (
                <a
                  key={href}
                  href={href}
                  onClick={() => setMenuOpen(false)}
                  className="landing-nav-link py-2.5"
                >
                  {label}
                </a>
              ))}
            </nav>
            <AuthButtons mobile />
          </div>
        )}
      </header>

      <main>
        {/* ================= HERO ================= */}
        <section className="relative lg:flex lg:min-h-[calc(100svh-4rem)] lg:items-center">
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[var(--accent)] opacity-[0.035]" />
          <div className="hero-enter relative mx-auto grid w-full max-w-7xl items-start gap-10 px-4 pt-12 pb-20 sm:px-6 sm:pt-16 lg:grid-cols-[1.08fr_0.92fr] lg:items-center lg:gap-14 lg:px-8 lg:py-8">
            {/* copy: headline, one short line, one CTA */}
            <div className="lg:-translate-y-8">
              <h1 className="text-balance text-[1.9rem] leading-[1.16] font-semibold tracking-[-0.01em] text-zinc-900 sm:text-[2.6rem] lg:text-[clamp(2.35rem,3.3vw,2.95rem)] dark:text-zinc-100">
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

              <p className="landing-hero-copy mt-5">
                Thaan ho ya suit, udhaar ya cash. KapraOS stock, sale, khata aur profit sab ek hi jagah rakhta hai.
              </p>

              <div className="mt-7 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-5">
                <button
                  onClick={primaryCta}
                  disabled={isLoading}
                  className="clay-btn clay-btn-accent landing-btn landing-btn-primary inline-flex h-12 w-full cursor-pointer items-center justify-center gap-2 px-6 text-[15px] disabled:opacity-60 sm:w-auto"
                >
                  {isLoading ? 'Loading…' : isAuthenticated ? 'Open Dashboard' : 'Apni dukaan joro'}
                  <ArrowRight className="h-4.5 w-4.5" />
                </button>

                {!isLoading &&
                  (isAuthenticated ? (
                    <a href="#features" className="landing-text-link">
                      Explore features ↓
                    </a>
                  ) : (
                    <Link to="/login" className="landing-text-link">
                      Already have an account? Sign in
                    </Link>
                  ))}
              </div>

              <p className="mt-10 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs font-medium text-zinc-700 dark:text-zinc-100">
                <span>POS billing</span>
                <span aria-hidden="true" className="text-zinc-400 dark:text-zinc-500">·</span>
                <span>Udhaar khata</span>
                <span aria-hidden="true" className="text-zinc-400 dark:text-zinc-500">·</span>
                <span>Daily P&amp;L</span>
              </p>
            </div>

            {/* visual: offset boutique photo with two purposeful clay tags */}
            <div className="relative mx-auto w-full max-w-[34rem] pb-12 lg:max-w-none">
              <ClayCard size="lg" className="relative overflow-hidden">
                <img
                  src="/pexels-arina-dmitrieva-66352626-14440412.webp"
                  alt="Boutique suit and woven shawl hanging in a fabric shop, managed in KapraOS"
                  width={1200}
                  height={1812}
                  className="h-[25rem] w-full object-cover sm:h-[30rem] lg:h-[min(70svh,560px)]"
                  loading="eager"
                  decoding="async"
                  fetchPriority="high"
                />
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-0 rounded-[inherit] shadow-[inset_0_1px_0_rgb(255_255_255_/_0.22),inset_0_-48px_56px_-52px_rgb(20_20_20_/_0.55)]"
                />
              </ClayCard>

              {/* price tag: small, overlapping the left edge */}
              <ClayCard
                size="sm"
                className="absolute top-7 -left-2 z-10 min-w-[8.5rem] rotate-[-1.5deg] px-3.5 py-2.5"
              >
                <p className="landing-micro-label">Lawn 3pc</p>
                <p className="font-tabular text-base leading-none font-bold text-zinc-900 dark:text-zinc-100">Rs 4,850</p>
                <p className="mt-1 text-[10px] text-zinc-500 dark:text-zinc-400">Meter cut · 2.5 m</p>
              </ClayCard>

              {/* sale card: larger, bottom-right edge overlap */}
              <ClayCard
                size="md"
                className="absolute -right-4 -bottom-1.5 w-[15.5rem] max-w-[calc(100%-1rem)] rotate-[1deg] px-4 py-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="landing-micro-label">Aaj ki sale</p>
                  <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 dark:text-emerald-400">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    Live
                  </span>
                </div>
                <p className="font-tabular mt-1 text-xl leading-none font-bold text-zinc-900 dark:text-zinc-100">
                  Rs 86,400
                </p>
                <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">23 bills · 6 udhaar</p>
              </ClayCard>
            </div>
          </div>
        </section>

        {/* ================= PAIN TO RELIEF ================= */}
        <section className="mx-auto max-w-7xl px-4 landing-section sm:px-6 lg:px-8">
          <div className="feature-reveal grid items-center gap-10 xl:grid-cols-[0.95fr_1.05fr] xl:gap-12">
            <div>
              <Eyebrow>Dher se system tak</Eyebrow>
              <SectionHeading>
                Kapron ke dher mein{' '}
                <span className="text-[var(--accent-ink)] dark:text-[var(--accent)]">hisaab</span>{' '}
                nahi dhoondna parta.
              </SectionHeading>
              <SectionCopy>
                Roz shaam copy mein sale jorna aur udhaar yaad rakhna, ye thakaan khatam. KapraOS ye teenon kaam khud
                karta hai.
              </SectionCopy>
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
            </div>

            <div className="relative">
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_15rem] sm:items-stretch sm:gap-5">
                <ClayCard className="order-1 overflow-hidden">
                  <div className="flex items-center justify-between gap-3 border-b border-[var(--clay-border-soft)] px-4 py-4 sm:px-5">
                    <div>
                      <p className="text-sm leading-tight font-semibold">Aaj ka hisaab</p>
                      <p className="landing-micro-label mt-0.5">Shaam ka closing</p>
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
                        {
                          time: '02:10',
                          item: 'Ahmed Sahab, udhaar wapsi',
                          amount: '+ Rs 15,000',
                          positive: true,
                          badge: '',
                        },
                        {
                          time: '04:30',
                          item: 'Kharcha, chai aur bijli',
                          amount: 'Rs 4,200',
                          positive: false,
                          badge: '',
                        },
                        {
                          time: 'Stock',
                          item: 'Kam stock, Lawn Sky Blue',
                          amount: '',
                          positive: false,
                          badge: '4m bacha',
                        },
                      ].map((row) => (
                        <div
                          key={`${row.time}-${row.item}`}
                          className="grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-baseline gap-3 py-2.5"
                        >
                          <span className="font-tabular text-[11px] text-zinc-500 dark:text-zinc-400">{row.time}</span>
                          <p className="min-w-0 text-[13px] leading-snug text-zinc-700 dark:text-zinc-300">{row.item}</p>
                          <div className="text-right">
                            {row.badge ? (
                              <Chip tone="accent">{row.badge}</Chip>
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

                    <ClayCard
                      size="sm"
                      tone="soft"
                      className="mt-3 flex items-center justify-between gap-3 px-4 py-3"
                    >
                      <p className="text-[13px] font-medium">Net profit, aaj</p>
                      <p className="font-tabular text-[15px] font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)]">
                        Rs 19,750
                      </p>
                    </ClayCard>
                  </div>
                </ClayCard>

                <div className="order-2 flex flex-col sm:min-h-0">
                  <figure className="landing-clay-card landing-radius-md overflow-hidden sm:flex-1 sm:min-h-0">
                    <img
                      src="/pexels-teona-swift-6850486.webp"
                      alt="Kapron ke dher mein dukandaar, pehle ka haal"
                      width={1600}
                      height={1066}
                      className="aspect-[16/10] w-full object-cover object-[72%_60%] sm:aspect-auto sm:h-full"
                      loading="lazy"
                      decoding="async"
                    />
                  </figure>
                  <ClayCard size="sm" className="mt-3 shrink-0 px-3.5 py-3">
                    <p className="landing-micro-label whitespace-nowrap text-[var(--accent-ink)] dark:text-[var(--accent)]">
                      Pehle, har dukaan ki kahani
                    </p>
                    <p className="mt-1.5 text-[13px] leading-relaxed font-normal text-zinc-600 dark:text-zinc-400">
                      “Maal kahan rakha tha? Kis ne udhaar liya tha? Register ka panna kahan gaya?”
                    </p>
                  </ClayCard>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ================= FEATURES BENTO ================= */}
        <section id="features" className="border-y border-[var(--clay-border-soft)]">
          <div className="mx-auto max-w-7xl px-4 landing-section sm:px-6 lg:px-8">
            <div className="max-w-2xl">
              <Eyebrow>Jo dukaan chalata hai</Eyebrow>
              <SectionHeading>Counter se godown tak, poora control.</SectionHeading>
              <SectionCopy>Chhe modules, ek hi hisaab. Har entry traceable.</SectionCopy>
            </div>

            <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5 lg:grid-cols-4">
              {features.map((f, i) => {
                const isPos = f.layout === 'featured';
                const layoutClass = isPos
                  ? 'p-6 sm:col-span-2 sm:p-7 lg:col-span-2 lg:row-span-2'
                  : f.layout === 'wide'
                    ? 'p-5 sm:col-span-2 sm:p-6 lg:col-span-2'
                    : f.layout === 'wide-end'
                      ? 'p-5 sm:p-6 lg:col-span-3'
                      : 'p-5 sm:p-6';

                return (
                  <article
                    key={f.title}
                    style={{ animationDelay: `${i * 45}ms` }}
                    className={`feature-card feature-reveal flex flex-col ${
                      isPos ? 'landing-radius-lg' : 'landing-radius-md'
                    } ${layoutClass}`}
                  >
                    <f.icon
                      className={`shrink-0 text-[var(--accent)] ${isPos ? 'h-6 w-6' : 'h-5 w-5'}`}
                      strokeWidth={1.75}
                    />
                    <h3 className={`mt-3 font-semibold leading-snug ${isPos ? 'text-xl sm:text-2xl' : 'text-[15px]'}`}>
                      {f.title}
                    </h3>
                    <p className="mt-1.5 max-w-md text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">{f.desc}</p>

                    {isPos && (
                      <div className="mt-6 flex flex-1 items-end">
                        <div className="landing-clay-card landing-clay-card-soft landing-radius-sm w-full p-3.5">
                          <div className="grid grid-cols-[1fr_56px_88px] items-center gap-2 border-b border-dashed border-[var(--clay-border)] pb-2">
                            <span className="landing-micro-label">Bill #1042</span>
                            <span aria-hidden="true" />
                            <span className="text-right text-[11px] text-zinc-500 dark:text-zinc-400">Aaj</span>
                          </div>
                          <div className="grid grid-cols-[1fr_56px_88px] items-center gap-2 border-b border-[var(--clay-border-soft)] pt-2 pb-1.5 text-[10px] font-medium tracking-[0.12em] text-zinc-500 uppercase dark:text-zinc-400">
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
                                <span className="font-tabular text-right text-[11px] text-zinc-500 dark:text-zinc-400">
                                  {qty}
                                </span>
                                <span className="font-tabular text-right font-semibold">Rs {amount}</span>
                              </li>
                            ))}
                          </ul>
                          <div className="grid grid-cols-[1fr_56px_88px] items-center gap-2 border-t border-[var(--clay-border)] pt-2.5">
                            <span className="landing-micro-label">Total</span>
                            <span aria-hidden="true" />
                            <span className="font-tabular text-right text-sm font-semibold">Rs 7,300</span>
                          </div>
                          <div className="mt-2.5 flex items-center gap-1.5">
                            <Chip tone="accent" className="gap-1">
                              <Check className="h-3 w-3" strokeWidth={3} />
                              Cash
                            </Chip>
                            <Chip>Easypaisa</Chip>
                          </div>
                        </div>
                      </div>
                    )}

                    {f.detail === 'flow' && (
                      <div className="mt-auto pt-3">
                        <div className="flex min-h-11 flex-wrap items-center gap-1.5 border-t border-[var(--clay-border-soft)] pt-3">
                          <Chip>Lawn thaan</Chip>
                          <span aria-hidden="true" className="text-[11px] text-zinc-500 dark:text-zinc-400">→</span>
                          <Chip>Cut</Chip>
                          <span aria-hidden="true" className="text-[11px] text-zinc-500 dark:text-zinc-400">→</span>
                          <Chip>3pc suit</Chip>
                        </div>
                      </div>
                    )}

                    {f.detail === 'stock' && (
                      <div className="mt-auto flex min-h-11 items-center justify-between gap-3 border-t border-[var(--clay-border-soft)] pt-3">
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium">Lawn Sky Blue</p>
                          <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">Meter stock</p>
                        </div>
                        <Chip tone="accent">4m bacha</Chip>
                      </div>
                    )}

                    {f.detail === 'khata' && (
                      <div className="mt-auto flex min-h-11 items-center justify-between gap-3 border-t border-[var(--clay-border-soft)] pt-3">
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium">Ayesha Bibi</p>
                          <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">12 din se baqaya</p>
                        </div>
                        <span className="font-tabular shrink-0 text-xs font-semibold text-[var(--accent-ink)]">
                          Rs 9,050
                        </span>
                      </div>
                    )}

                    {f.detail === 'supplier' && (
                      <div className="mt-auto flex min-h-11 items-center justify-between gap-3 border-t border-[var(--clay-border-soft)] pt-3">
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium">Imran Fabrics</p>
                          <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">Is hafte dena hai</p>
                        </div>
                        <span className="font-tabular shrink-0 text-xs font-semibold text-[var(--accent-ink)] dark:text-[var(--accent)]">
                          Rs 48,000
                        </span>
                      </div>
                    )}

                    {f.detail === 'pl' && (
                      <div className="mt-auto pt-4">
                        <div className="grid grid-cols-2 gap-x-4 border-t border-[var(--clay-border-soft)] pt-3 lg:grid-cols-4 lg:gap-x-0">
                          {[
                            { label: 'Sale', value: 'Rs 86,400', accent: false },
                            { label: 'COGS', value: 'Rs 62,450', accent: false },
                            { label: 'Kharcha', value: 'Rs 4,200', accent: false },
                            { label: 'Net profit', value: 'Rs 19,750', accent: true },
                          ].map((item, idx) => {
                            const divider =
                              idx === 0
                                ? ''
                                : idx === 1
                                  ? 'border-l border-[var(--clay-border-soft)] pl-3'
                                  : idx === 2
                                    ? 'border-t border-[var(--clay-border-soft)] pt-3 lg:border-t-0 lg:border-l lg:pl-3 lg:pt-0'
                                    : 'border-t border-l border-[var(--clay-border-soft)] pt-3 pl-3 lg:border-t-0 lg:pt-0';

                            return (
                              <div key={item.label} className={divider}>
                                <p className="text-[10px] font-medium tracking-[0.12em] text-zinc-500 uppercase dark:text-zinc-400">
                                  {item.label}
                                </p>
                                <p
                                  className={`font-tabular mt-0.5 text-sm font-semibold ${
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
        <section className="mx-auto max-w-7xl px-4 landing-section sm:px-6 lg:px-8">
          <div className="feature-reveal grid items-center gap-10 lg:grid-cols-2 lg:gap-12">
            <div className="lg:order-2">
              <Eyebrow>Thaan ho ya boutique piece</Eyebrow>
              <SectionHeading>Har design, har color, har meter, gin ke rakho.</SectionHeading>
              <SectionCopy>
                Wo sky-blue wala lawn khatam hua ya nahi? KapraOS mein har color ka apna variant hai, stock khud ghat-ta
                barhta hai.
              </SectionCopy>
              <div className="mt-5 flex flex-wrap gap-2">
                {['Open fabric', 'Ready suit', 'Boutique piece'].map((tag) => (
                  <Chip key={tag}>{tag}</Chip>
                ))}
              </div>
            </div>

            <div className="lg:order-1">
              <div className="relative mx-auto flex w-full max-w-[26rem] flex-col lg:block lg:max-w-none">
                <ClayCard
                  size="lg"
                  className="mx-auto w-full max-w-[26rem] overflow-hidden lg:mx-0 lg:w-[60%] lg:max-w-none lg:-rotate-[0.5deg]"
                >
                  <img
                    src="/pexels-arina-dmitrieva-66352626-14440420.webp"
                    alt="Embroidered waistcoat and folded fabric stacks in a kapra shop"
                    width={1000}
                    height={1510}
                    className="aspect-[3/4] w-full object-cover object-[50%_50%] saturate-[0.88]"
                    loading="lazy"
                    decoding="async"
                  />
                </ClayCard>

                <ClayCard
                  size="md"
                  className="relative z-10 mx-auto -mt-6 w-full max-w-[24rem] p-4 sm:p-5 lg:absolute lg:right-0 lg:bottom-0 lg:mt-0 lg:w-[56%] lg:max-w-none lg:rotate-[0.4deg]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm leading-tight font-semibold">Lawn, printed</p>
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
                          {variant.low && <Chip tone="accent">Kam stock</Chip>}
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
                </ClayCard>
              </div>
            </div>
          </div>
        </section>

        {/* ================= KHATA (product ledger mock) ================= */}
        <section id="khata" className="border-y border-[var(--clay-border-soft)]">
          <div className="mx-auto max-w-7xl px-4 landing-section sm:px-6 lg:px-8">
            <div className="feature-reveal grid items-center gap-10 lg:grid-cols-2 lg:gap-12">
              <div className="min-w-0">
                <Eyebrow>Udhaar, ab diary mein nahi</Eyebrow>
                <SectionHeading>Har khata, apne aap update.</SectionHeading>
                <SectionCopy>
                  Udhaar sale hote hi customer ke khate mein chadh jata hai. Supplier se maal liya to payable bhi saath
                  bante hain.
                </SectionCopy>

                <ClayCard
                  tone="soft"
                  className="mt-6 grid grid-cols-2 divide-x divide-[var(--clay-border-soft)]"
                >
                  <div className="min-w-0 px-3 py-3.5 sm:px-4 sm:py-4">
                    <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                      <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300">Lena hai</span>
                      <span className="text-[10px] text-zinc-500 dark:text-zinc-400">customers</span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                      <span className="font-tabular text-lg font-semibold text-[var(--accent-ink)] sm:text-xl dark:text-[var(--accent)]">
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
                      <span className="font-tabular text-lg font-semibold text-zinc-900 sm:text-xl dark:text-zinc-100">
                        Rs 48,000
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                      Imran Fabrics, is hafte
                    </p>
                  </div>
                </ClayCard>

                <blockquote className="mt-7 flex items-start gap-2.5">
                  <BookOpenText
                    aria-hidden="true"
                    className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent-ink)] dark:text-[var(--accent)]"
                    strokeWidth={1.75}
                  />
                  <div className="min-w-0">
                    <p className="text-sm leading-relaxed font-normal text-zinc-800 dark:text-zinc-200">
                      “Baji, aapka baqaya Rs 9,050 hai. Ye suit us mein jor dun?”
                    </p>
                    <p className="mt-1.5 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
                      Khata khol ke baat karo. Customer ka bharosa, dukaan ki ronak.
                    </p>
                  </div>
                </blockquote>
              </div>

              <div className="min-w-0">
                <ClayCard className="overflow-hidden">
                  <div className="flex items-center justify-between gap-4 border-b border-[var(--clay-border-soft)] px-4 py-3.5 sm:px-5">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] text-sm font-semibold text-[var(--accent-ink)] shadow-[var(--clay-highlight),var(--clay-shadow-sm)] dark:text-[var(--accent)]">
                        A
                      </span>
                      <div className="min-w-0">
                        <p className="truncate text-sm leading-tight font-semibold">Ayesha Bibi, khata</p>
                        <p className="mt-0.5 truncate text-[11px] text-zinc-500 dark:text-zinc-400">
                          Baqaya aur recovery
                        </p>
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-[9px] font-medium tracking-[0.16em] text-zinc-500 uppercase dark:text-zinc-400">
                        baqaya
                      </p>
                      <p className="font-tabular mt-0.5 text-lg leading-none font-semibold">Rs 9,050</p>
                    </div>
                  </div>

                  <div className="divide-y divide-[var(--clay-border-soft)] px-4 sm:px-5">
                    {[
                      {
                        date: '12 Feb',
                        label: 'Lawn 3pc, udhaar',
                        amount: 'Rs 6,200',
                        balance: '6,200',
                        positive: false,
                      },
                      {
                        date: '18 Feb',
                        label: 'Payment mili',
                        amount: '- Rs 2,000',
                        balance: '4,200',
                        positive: true,
                      },
                      {
                        date: '24 Feb',
                        label: 'Embroidered kurta, udhaar',
                        amount: 'Rs 4,850',
                        balance: '9,050',
                        positive: false,
                      },
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
                          <p className="font-tabular mt-0.5 text-[10px] text-zinc-500 dark:text-zinc-400">
                            baqaya {row.balance}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="flex flex-col gap-3 border-t border-[var(--clay-border-soft)] px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                    <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                      POS se khud post. Diary ki zaroorat nahi.
                    </p>
                    <span className="clay-btn clay-btn-accent landing-btn landing-btn-primary inline-flex shrink-0 items-center justify-center px-4 py-2 text-xs">
                      Recover Rs 2,000
                    </span>
                  </div>
                </ClayCard>
              </div>
            </div>
          </div>
        </section>

        {/* ================= HOW IT WORKS ================= */}
        <section id="how" className="mx-auto max-w-7xl px-4 landing-section sm:px-6 lg:px-8">
          <div className="feature-reveal">
            <div className="max-w-2xl">
              <Eyebrow>Subah se shaam tak</Eyebrow>
              <SectionHeading>Dukaan ka routine, KapraOS ke saath.</SectionHeading>
            </div>

            {/* One clay rail, three moments of the day */}
            <ClayCard size="lg" className="mt-8 p-7 sm:mt-10 sm:p-8">
              <div className="relative">
                <span
                  aria-hidden="true"
                  className="absolute top-1 right-[calc(33.333%-1.6875rem)] left-[3px] hidden h-px bg-[var(--clay-border)] sm:block lg:right-[calc(33.333%-1.9375rem)]"
                />
                <ol className="grid gap-0 sm:grid-cols-3">
                  {steps.map((s, i) => (
                    <li
                      key={s.time}
                      className={`relative min-w-0 py-5 first:pt-0 last:pb-0 sm:border-l sm:border-[var(--clay-border-soft)] sm:py-0 sm:pl-6 lg:pl-7 ${
                        i === 0 ? 'sm:border-l-0 sm:pl-0' : ''
                      } ${i > 0 ? 'border-t border-[var(--clay-border-soft)] sm:border-t-0' : ''}`}
                    >
                      <div className="flex h-2 items-center">
                        <span
                          aria-hidden="true"
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)] ring-4 ring-[var(--clay-surface)]"
                        />
                      </div>
                      <Eyebrow className="mt-2">{s.time}</Eyebrow>
                      <h3 className="mt-2 text-[15px] leading-snug font-semibold">{s.title}</h3>
                      <p className="mt-1.5 max-w-[34ch] text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
                        {s.desc}
                      </p>
                    </li>
                  ))}
                </ol>
              </div>
            </ClayCard>

            {/* AI Munshi: flat clay, no glass */}
            <ClayCard
              id="ai"
              size="lg"
              className="mt-12 px-6 py-8 sm:mt-14 sm:px-8 sm:py-9 lg:mt-16 lg:px-10 lg:py-10"
            >
              <div className="grid items-center gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:gap-12">
                <div className="min-w-0">
                  <Eyebrow>AI Munshi, aapke data par</Eyebrow>
                  <SectionHeading>Bas poocho: Aaj kitni sale hui?</SectionHeading>
                  <SectionCopy>
                    AI Munshi aapka asal stock, sale aur khata dekh kar jawab deta hai. Kam stock ka alert, udhaar
                    reminder, profit ka summary.
                  </SectionCopy>

                  <div className="mt-5 flex flex-wrap gap-2">
                    {[
                      'Lawn mein kya khatam ho raha hai?',
                      'Ahmed ka udhaar kitna hai?',
                      'Is hafte ka profit batao',
                    ].map((q) => (
                      <button
                        key={q}
                        type="button"
                        onClick={openAi}
                        className="landing-chip landing-chip-button"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="w-full min-w-0 max-w-[30rem] lg:justify-self-end">
                  <div className="landing-clay-card landing-clay-card-soft landing-radius-md p-4 sm:p-5">
                    <div className="flex items-center gap-2.5 border-b border-[var(--clay-border-soft)] pb-3">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--accent)] text-[13px] font-semibold text-[var(--accent-on)] shadow-[var(--clay-highlight),var(--clay-shadow-sm)]">
                        M
                      </span>
                      <div className="flex min-w-0 items-center gap-2">
                        <p className="truncate text-sm font-medium">AI Munshi</p>
                        <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-700 dark:text-emerald-400">
                          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                          online
                        </span>
                      </div>
                    </div>

                    <div className="space-y-3 pt-3 text-sm">
                      <div className="landing-bubble-user px-3.5 py-2.5">Aaj kitni sale hui?</div>
                      <div className="landing-bubble-ai px-3.5 py-2.5">
                        Aaj <span className="font-tabular font-medium">Rs 86,400</span> ki{' '}
                        <span className="font-tabular font-medium">23</span> sales hui.{' '}
                        <span className="font-tabular font-medium">6</span> udhaar (
                        <span className="font-tabular font-medium">Rs 21,300</span>). Net profit{' '}
                        <span className="font-tabular font-medium">Rs 19,750</span>.
                      </div>
                      <div className="landing-bubble-user px-3.5 py-2.5">Aur kya khatam ho raha hai?</div>
                      <div className="landing-bubble-ai px-3.5 py-2.5">
                        Lawn Sky Blue sirf <span className="font-tabular font-medium">4m</span> bacha hai. Dobara
                        order kar lo?
                      </div>
                    </div>

                    <div className="landing-radius-sm mt-3 flex items-center gap-2 border border-[var(--clay-border)] bg-[var(--clay-surface)] py-1.5 pr-1.5 pl-3 shadow-[var(--clay-input-inset)]">
                      <input
                        type="text"
                        aria-label="AI Munshi se sawal poochho"
                        placeholder="Kuch poochho..."
                        className="h-8 min-w-0 flex-1 bg-transparent text-sm text-zinc-800 placeholder:text-zinc-500 dark:text-zinc-100 dark:placeholder:text-zinc-400"
                      />
                      <button
                        type="button"
                        onClick={openAi}
                        aria-label="Send message"
                        className="clay-btn clay-btn-accent landing-btn landing-btn-primary inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center"
                      >
                        <Send className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </ClayCard>
          </div>
        </section>

        {/* ================= FINAL CTA ================= */}
        <section className="mx-auto max-w-7xl px-4 pb-8 sm:px-6 lg:px-8 lg:pb-12">
          <div className="landing-cta landing-radius-lg relative overflow-hidden">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 bg-[var(--accent)] opacity-[0.035]"
            />
            <div className="feature-reveal relative grid gap-7 px-4 py-9 sm:px-10 sm:py-10 lg:grid-cols-[minmax(0,1fr)_29rem] lg:items-center lg:gap-10 lg:px-[60px] lg:py-11">
              <div className="min-w-0">
                <Eyebrow tone="inverse">Aaj se shuru karo</Eyebrow>
                <SectionHeading tone="inverse">
                  {isAuthenticated ? (
                    <>
                      <span className="inline-block">Dashboard tayyar hai,</span>{' '}
                      <span className="inline-block">dukaan kholo.</span>
                    </>
                  ) : (
                    'Aaj hi apni dukaan KapraOS par lao.'
                  )}
                </SectionHeading>
                <SectionCopy tone="inverse" wide>
                  Stock, sale, khata aur profit, sab ek jagah. Ek counter ho ya teen godown.
                </SectionCopy>
              </div>

              <div className="flex min-w-0 flex-col items-start gap-3 lg:items-end lg:justify-self-end">
                <button
                  onClick={primaryCta}
                  disabled={isLoading}
                  className="clay-btn clay-btn-accent landing-btn landing-btn-primary inline-flex h-11 cursor-pointer items-center justify-center gap-2 px-6 text-sm disabled:cursor-not-allowed disabled:opacity-60 sm:text-[15px]"
                >
                  {isLoading ? 'Loading…' : isAuthenticated ? 'Open Dashboard' : 'Create free account'}
                  <ArrowRight className="h-4 w-4" />
                </button>
                <ul className="mt-1 flex flex-wrap gap-1.5 lg:justify-end">
                  {['Credit card nahi chahiye', 'Urdu-friendly', 'Mobile par bhi chalega'].map((chip) => (
                    <Chip key={chip} as="li" tone="inverse">
                      {chip}
                    </Chip>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* ================= FOOTER ================= */}
      <footer className="border-t border-[var(--clay-border-soft)] bg-[var(--clay-bg)]">
        <div className="footer-reveal mx-auto max-w-7xl px-4 pt-8 pb-6 sm:px-6 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start lg:gap-x-16">
            <div className="min-w-0">
              <div className="flex items-center gap-2.5">
                <span className="clay-icon-tile h-9 w-9 border-transparent bg-[var(--accent)] text-[var(--accent-on)]">
                  <Store className="h-4.5 w-4.5" />
                </span>
                <span className="text-[15px] font-bold tracking-tight">KapraOS</span>
              </div>
              <p className="mt-2.5 max-w-[32ch] text-pretty text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
                Kapra dukaan ka hisaab, ek jagah.
              </p>
              <button
                onClick={primaryCta}
                disabled={isLoading}
                className="clay-btn landing-btn landing-btn-secondary mt-3 inline-flex h-9 cursor-pointer items-center justify-center gap-2 px-4 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isLoading ? 'Loading…' : isAuthenticated ? 'Open Dashboard' : 'Create free account'}
                <ArrowRight className="h-4 w-4" />
              </button>
            </div>

            <nav
              aria-label="Footer"
              className="grid grid-cols-2 gap-x-8 gap-y-7 max-[360px]:grid-cols-1 md:gap-x-16 lg:justify-self-end"
            >
              <FooterCol
                title="Product"
                links={[
                  ['Products & Variants', '/products'],
                  ['Inventory', '/inventory'],
                  ['New Sale (POS)', '/sales/new'],
                  ['Expenses', '/expenses'],
                ]}
              />
              <FooterCol
                title="Hisaab"
                links={[
                  ['Khata system', '#khata'],
                  ['Daily Reports', '/reports'],
                  ['Analytics', '/analytics'],
                  ['AI Munshi', '#ai'],
                ]}
              />
            </nav>
          </div>

          <div className="mt-7 flex flex-col gap-3 text-xs text-zinc-500 sm:flex-row sm:items-center sm:justify-between dark:text-zinc-400">
            <p>© {new Date().getFullYear()} KapraOS. Sab haq mehfooz.</p>
            <ul className="flex flex-wrap gap-1.5">
              {['Easypaisa / JazzCash ready', 'Meter, yard aur piece', 'Cash, card, bank transfer'].map((chip) => (
                <Chip key={chip} as="li">
                  {chip}
                </Chip>
              ))}
            </ul>
          </div>
        </div>
      </footer>
    </div>
  );
}

function FooterCol({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <div>
      <p className="text-xs font-semibold text-zinc-500 dark:text-zinc-400">{title}</p>
      <ul className="mt-2.5 space-y-1.5">
        {links.map(([label, href]) => (
          <li key={label + href}>
            {href.startsWith('#') ? (
              <a href={href} className="landing-footer-link">
                {label}
              </a>
            ) : (
              <Link to={href} className="landing-footer-link">
                {label}
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default LandingPage;
