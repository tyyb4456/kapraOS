import type { HTMLAttributes, ReactNode } from 'react';

type Tone = 'default' | 'inverse';

type EyebrowProps = {
  children: ReactNode;
  tone?: Tone;
  className?: string;
};

export function Eyebrow({ children, tone = 'default', className = '' }: EyebrowProps) {
  return (
    <p className={`landing-eyebrow ${tone === 'inverse' ? 'landing-eyebrow-inverse' : ''} ${className}`}>
      {children}
    </p>
  );
}

export function SectionHeading({
  children,
  tone = 'default',
  className = '',
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <h2 className={`landing-section-heading ${tone === 'inverse' ? 'landing-section-heading-inverse' : ''} ${className}`}>
      {children}
    </h2>
  );
}

type SectionCopyProps = {
  children: ReactNode;
  tone?: Tone;
  wide?: boolean;
  className?: string;
};

export function SectionCopy({ children, tone = 'default', wide = false, className = '' }: SectionCopyProps) {
  return (
    <p
      className={`landing-section-copy ${wide ? 'landing-section-copy-wide' : ''} ${
        tone === 'inverse' ? 'landing-section-copy-inverse' : ''
      } ${className}`}
    >
      {children}
    </p>
  );
}

type ChipProps = {
  children: ReactNode;
  tone?: Tone | 'accent';
  as?: 'span' | 'li';
  className?: string;
};

export function Chip({ children, tone = 'default', as: Tag = 'span', className = '' }: ChipProps) {
  const toneClass = tone === 'accent' ? 'landing-chip-accent' : tone === 'inverse' ? 'landing-chip-inverse' : '';
  return <Tag className={`landing-chip ${toneClass} ${className}`}>{children}</Tag>;
}

type ClayCardProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  tone?: 'surface' | 'soft';
};

export function ClayCard({ children, size = 'md', tone = 'surface', className = '', ...props }: ClayCardProps) {
  const radius = size === 'sm' ? 'landing-radius-sm' : size === 'lg' ? 'landing-radius-lg' : 'landing-radius-md';
  return (
    <div
      className={`landing-clay-card ${tone === 'soft' ? 'landing-clay-card-soft' : ''} ${radius} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}
