import { Loader2 } from 'lucide-react';

export interface LoadingStateProps {
  message?: string;
  className?: string;
}

export function LoadingState({
  message = 'Loading data...',
  className = '',
}: LoadingStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center p-12 text-center text-zinc-500 space-y-3 ${className}`}
    >
      <Loader2 className="w-6 h-6 animate-spin text-zinc-600" />
      <p className="text-xs font-medium text-zinc-600">{message}</p>
    </div>
  );
}
