import React from 'react';

export function Table({ className = '', children, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto border border-zinc-200 rounded-mg bg-white">
      <table className={`w-full caption-bottom text-sm ${className}`} {...props}>
        {children}
      </table>
    </div>
  );
}

export function TableHeader({ className = '', children, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead className={`border-b border-zinc-200 bg-zinc-50/80 text-xs font-medium text-zinc-600 ${className}`} {...props}>
      {children}
    </thead>
  );
}

export function TableBody({ className = '', children, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tbody className={`divide-y divide-zinc-200/75 ${className}`} {...props}>
      {children}
    </tbody>
  );
}

export function TableRow({ className = '', children, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={`transition-colors hover:bg-zinc-50/60 data-[state=selected]:bg-zinc-50 ${className}`} {...props}>
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
      className={`h-9 px-3.5 text-xs font-semibold text-zinc-700 tracking-normal ${alignClass} ${className}`}
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
    <td className={`p-3.5 align-middle text-zinc-800 ${alignClass} ${className}`} {...props}>
      {children}
    </td>
  );
}
