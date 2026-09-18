import React from 'react';

export type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'brand' | 'secondary' | 'destructive';
export type BadgeSize = 'sm' | 'md';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
}

const variantStyles: Record<BadgeVariant, string> = {
  neutral: 'bg-zinc-100 text-zinc-700 border-zinc-200',
  success: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  warning: 'bg-amber-50 text-amber-700 border-amber-200',
  danger: 'bg-rose-50 text-rose-700 border-rose-200',
  info: 'bg-sky-50 text-sky-700 border-sky-200',
  brand: 'bg-slate-900 text-white border-slate-900',
  secondary: 'bg-zinc-100 text-zinc-700 border-zinc-200',
  destructive: 'bg-rose-50 text-rose-700 border-rose-200',
};

const sizeStyles: Record<BadgeSize, string> = {
  sm: 'px-2 py-0.5 text-[11px] font-medium leading-tight',
  md: 'px-2.5 py-0.5 text-xs font-medium leading-tight',
};

export function Badge({
  className = '',
  variant = 'neutral',
  size = 'md',
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={`binline-flex items-center gap-1 rounded-full border ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}
      {...props}
    >
      {children}
    </span>
  );
}
