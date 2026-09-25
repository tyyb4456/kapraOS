import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Button } from '../ui/Button.tsx';

export interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  className = '',
}: ErrorStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center p-8 text-center rounded-lg border border-rose-200 dark:border-rose-500/30 bg-rose-50/50 dark:bg-rose-500/10 max-w-md mx-auto my-6 space-y-3 ${className}`}
    >
      <div className="w-10 h-10 rounded-full bg-rose-100 dark:bg-rose-500/15 flex items-center justify-center text-rose-600 dark:text-rose-300">
        <AlertTriangle className="w-5 h-5" />
      </div>
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-rose-900 dark:text-rose-200">{title}</h3>
        <p className="text-xs text-rose-700 dark:text-rose-300">{message}</p>
      </div>
      {onRetry && (
        <Button
          variant="outline"
          size="sm"
          onClick={onRetry}
          leftIcon={<RotateCcw className="w-3.5 h-3.5" />}
          className="mt-2"
        >
          Retry
        </Button>
      )}
    </div>
  );
}
