import React from 'react';

export interface PageContainerProps extends React.HTMLAttributes<HTMLDivElement> {
  maxWidth?: 'default' | 'full' | 'narrow';
}

export function PageContainer({
  children,
  className = '',
  maxWidth = 'default',
  ...props
}: PageContainerProps) {
  const maxWidthClass =
    maxWidth === 'full'
      ? 'w-full'
      : maxWidth === 'narrow'
        ? 'max-w-5xl mx-auto'
        : 'max-w-7xl mx-auto';

  return (
    <div className={`p-4 sm:p-6 lg:p-8 space-y-6 ${maxWidthClass} ${className}`} {...props}>
      {children}
    </div>
  );
}
