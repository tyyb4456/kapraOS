import React, { forwardRef } from 'react';
import { Loader2 } from 'lucide-react';

export type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary:
    'clay-btn clay-btn-primary bg-[var(--clay-primary)] hover:bg-[var(--clay-primary-hover)] active:bg-[var(--clay-primary-active)] text-white border border-transparent focus-visible:ring-[var(--clay-primary)]',
  secondary:
    'clay-btn bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 hover:bg-zinc-50 dark:hover:bg-zinc-700 active:bg-zinc-100 dark:active:bg-zinc-800 border border-[var(--clay-border-soft)] dark:border-zinc-700 focus-visible:ring-[var(--clay-primary)]',
  outline:
    'clay-btn bg-white dark:bg-transparent text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 active:bg-zinc-100 dark:active:bg-zinc-800 border border-[var(--clay-border)] dark:border-zinc-700 focus-visible:ring-[var(--clay-primary)]',
  ghost:
    'bg-transparent text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 active:bg-zinc-200/60 dark:active:bg-zinc-800 border border-transparent focus-visible:ring-[var(--clay-primary)] rounded-[14px]',
  danger:
    'clay-btn bg-rose-600 text-white hover:bg-rose-700 active:bg-rose-800 border border-rose-600 focus-visible:ring-rose-600',
};

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'h-8 px-2.5 text-xs gap-1.5 rounded-xl',
  md: 'h-9 px-3.5 text-sm gap-2 rounded-xl',
  lg: 'h-10 px-4 text-base gap-2 rounded-xl',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className = '',
      variant = 'primary',
      size = 'md',
      isLoading = false,
      leftIcon,
      rightIcon,
      disabled,
      children,
      ...props
    },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={`inline-flex items-center justify-center font-medium transition-colors select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 disabled:opacity-50 disabled:pointer-events-none cursor-pointer ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}
        {...props}
      >
        {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : leftIcon}
        <span>{children}</span>
        {!isLoading && rightIcon}
      </button>
    );
  },
);

Button.displayName = 'Button';
