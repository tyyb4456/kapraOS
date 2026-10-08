import { useState, useRef, useEffect } from 'react';
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react';

interface DatePickerProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function pad(n: number) {
  return String(n).padStart(2, '0');
}

function toLocalISO(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function DatePicker({ value, onChange, placeholder = 'Pick a date' }: DatePickerProps) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<Date>(() => {
    if (value) {
      const d = new Date(value + 'T00:00:00');
      if (!isNaN(d.getTime())) return d;
    }
    return new Date();
  });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const today = new Date();
  const todayISO = toLocalISO(today);
  const selectedISO = value || '';

  const year = view.getFullYear();
  const month = view.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrev = new Date(year, month, 0).getDate();

  const cells: { date: Date; current: boolean }[] = [];
  for (let i = firstDay - 1; i >= 0; i--) {
    cells.push({ date: new Date(year, month - 1, daysInPrev - i), current: false });
  }
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push({ date: new Date(year, month, d), current: true });
  }
  while (cells.length % 7 !== 0) {
    const last = cells[cells.length - 1].date;
    cells.push({ date: new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1), current: false });
  }

  const monthLabel = view.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

  const display = value
    ? new Date(value + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    : '';

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="clay-input w-full h-9 px-3 text-sm flex items-center gap-2 text-left text-zinc-900 dark:text-zinc-100 transition-shadow focus:outline-none"
      >
        <Calendar className="w-4 h-4 text-zinc-400 shrink-0" />
        <span className={display ? '' : 'text-zinc-400 dark:text-zinc-500'}>
          {display || placeholder}
        </span>
      </button>

      {open && (
        <div className="absolute z-50 mt-2 w-72 rounded-2xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-xl p-3">
          <div className="flex items-center justify-between mb-2">
            <button
              type="button"
              onClick={() => setView(new Date(year, month - 1, 1))}
              className="p-1.5 rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{monthLabel}</span>
            <button
              type="button"
              onClick={() => setView(new Date(year, month + 1, 1))}
              className="p-1.5 rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-0.5 text-center">
            {WEEKDAYS.map((d) => (
              <div key={d} className="text-[11px] font-medium text-zinc-400 py-1">
                {d}
              </div>
            ))}
            {cells.map(({ date, current }, i) => {
              const iso = toLocalISO(date);
              const selected = iso === selectedISO;
              const isToday = iso === todayISO;
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => {
                    onChange(iso);
                    setView(date);
                    setOpen(false);
                  }}
                  className={`h-8 w-8 mx-auto rounded-lg text-xs transition-colors ${
                    selected
                      ? 'bg-[var(--clay-primary)] text-white font-semibold'
                      : isToday
                        ? 'border border-[var(--clay-primary)] text-[var(--clay-primary)] font-semibold'
                        : current
                          ? 'text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                          : 'text-zinc-300 dark:text-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/50'
                  }`}
                >
                  {date.getDate()}
                </button>
              );
            })}
          </div>

          <div className="flex items-center justify-between mt-2 pt-2 border-t border-zinc-100 dark:border-zinc-800">
            <button
              type="button"
              onClick={() => {
                onChange('');
                setOpen(false);
              }}
              className="text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => {
                onChange(todayISO);
                setView(new Date());
                setOpen(false);
              }}
              className="text-xs font-medium text-[var(--clay-primary)]"
            >
              Today
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
