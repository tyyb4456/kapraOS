import React from 'react';

export function Skeleton({ className = '', ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`animate-pulse rounded-xl bg-zinc-200/70 dark:bg-zinc-800 ${className}`} {...props} />;
}
