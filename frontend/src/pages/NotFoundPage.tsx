import { Link } from 'react-router-dom';
import { Home } from 'lucide-react';
import { Button } from '../components/ui/Button.tsx';

export function NotFoundPage() {
  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 flex flex-col items-center justify-center p-6 text-center">
      <div className="w-12 h-12 rounded-full bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center text-zinc-400 dark:text-zinc-500 mb-4">
        <span className="text-lg font-bold">404</span>
      </div>
      <h1 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50 mb-1">
        Page Not Found
      </h1>
      <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm mb-6">
        The application page or route you are looking for does not exist or has been moved.
      </p>
      <Link to="/dashboard">
        <Button
          variant="primary"
          size="sm"
          leftIcon={<Home className="w-4 h-4" />}
        >
          Return to Dashboard
        </Button>
      </Link>
    </div>
  );
}
