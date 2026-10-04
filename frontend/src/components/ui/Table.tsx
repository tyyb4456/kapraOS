import React from 'react';

export function Table({ className = '', children, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="clay-table-wrap w-full overflow-x-auto">
      <table className={`w-full caption-bottom text-sm ${className}`} {...props}>
        {children}
      </table>
    </div>
  );
}

export function TableHeader({ className = '', children, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead className={`clay-table-head border-b border-[var(--clay-border-soft)] text-xs font-medium text-zinc-600 dark:text-zinc-400 ${className}`} {...props}>
      {children}
    </thead>
  );
}

export function TableBody({ className = '', children, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tbody className={`divide-y divide-zinc-200/75 dark:divide-zinc-800 ${className}`} {...props}>
      {children}
    </tbody>
  );
}

export function TableRow({ className = '', children, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={`transition-colors hover:bg-zinc-50/60 dark:hover:bg-zinc-800/60 data-[state=selected]:bg-zinc-50 dark:data-[state=selected]:bg-zinc-800 ${className}`} {...props}>
      {children}
    </tr>
  );
}

export function TableHead({
  className = '',
  align = 'left',
  children,
  ...props
}: React.ThHTMLAttributes<HTMLTableCellElement> & { align?: 'left' | 'center' | 'right' }) {
  const alignClass = align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left';
  return (
    <th
      className={`h-9 px-3.5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 tracking-normal ${alignClass} ${className}`}
      {...props}
    >
      {children}
    </th>
  );
}

export function TableCell({
  className = '',
  align = 'left',
  children,
  ...props
}: React.TdHTMLAttributes<HTMLTableCellElement> & { align?: 'left' | 'center' | 'right' }) {
  const alignClass = align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left';
  return (
    <td className={`p-3.5 align-middle text-zinc-800 dark:text-zinc-200 ${alignClass} ${className}`} {...props}>
      {children}
    </td>
  );
}
