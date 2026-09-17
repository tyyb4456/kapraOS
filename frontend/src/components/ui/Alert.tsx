import React from 'react';
import { AlertCircle, CheckCircle2, Info, AlertTriangle } from 'lucide-react';

export type AlertVariant = 'info' | 'success' | 'warning' | 'danger';

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: AlertVariant;
  title?: string;
}

const variantConfig: Record<
  AlertVariant,
  { bg: string; border: string; text: string; icon: React.ComponentType<{ className?: string }> }
> = {
  info: {
    bg: 'bg-sky-50',
    border: 'border-sky-200',
    text: 'text-sky-800',
    icon: Info,
  },
  success: {
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    text: 'text-emerald-800',
    icon: CheckCircle2,
  },
  warning: {
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    text: 'text-amber-800',
    icon: AlertTriangle,
  },
  danger: {
    bg: 'bg-rose-50',
    border: 'border-rose-200',
    text: 'text-rose-800',
    icon: AlertCircle,
  },
};

export function Alert({ variant = 'info', title, children, className = '', ...props }: AlertProps) {
  const config = variantConfig[variant];
  const IconComponent = config.icon;

  return (
    <div
      role="alert"
      className={`flex items-start gap-3 rounded-lg border p-3.5 text-xs ${config.bg} ${config.border} ${config.text} ${className}`}
      {...props}
    >
      <IconComponent className="w-4 h-4 shrink-0 mt-0.5" />
      <div className="flex-1 space-y-0.5">
        {title && <h5 className="font-semibold">{title}</h5>}
        <div className="leading-relaxed">{children}</div>
      </div>
    </div>
  );
}
