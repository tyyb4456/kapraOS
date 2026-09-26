import { useState } from 'react';
import { Badge } from '../ui/index.ts';
import { Input } from '../ui/index.ts';

interface SaleApprovalCardProps {
  args: Record<string, unknown>;
  allowEdit: boolean;
  disabled: boolean;
  onEditChange: (edited: Record<string, unknown> | null) => void;
}

function str(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function fmtMoney(value: number): string {
  return `Rs. ${value.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function paymentLabel(args: Record<string, unknown>): string {
  const kind = str(args.payment_kind).toLowerCase();
  const method = str(args.payment_method) || 'cash';
  const paid = num(args.paid_amount);
  if (paid !== null && paid > 0) return `Partial — ${fmtMoney(paid)} now (${method})`;
  if (kind === 'cash') return `Cash — full payment now (${method})`;
  return 'Udhaar — nothing paid now';
}

function sameNumber(a: number | null, b: number | null): boolean {
  if (a === null || b === null) return a === b;
  return a === b;
}

/**
 * Human-readable approval card for the `create_sale` HITL action.
 *
 * Shows the shopkeeper what will actually be recorded (customer, product,
 * quantity, price, total, payment) instead of raw tool JSON. Optionally
 * allows correcting quantity / unit price before approval — the parent
 * sends the corrected values back as an `edit` decision with the FULL
 * argument set, which the backend re-validates like the original.
 */
export function SaleApprovalCard({ args, allowEdit, disabled, onEditChange }: SaleApprovalCardProps) {
  const origQty = str(args.quantity) || '1';
  const origPrice = str(args.unit_price ?? '');
  const [qtyDraft, setQtyDraft] = useState(origQty);
  const [priceDraft, setPriceDraft] = useState(origPrice);
  const [modified, setModified] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const pushEdit = (nextQty: string, nextPrice: string) => {
    const qty = num(nextQty);
    const price = nextPrice.trim() === '' ? null : num(nextPrice);
    if (qty === null || qty <= 0) {
      setEditError('Quantity must be a positive number.');
      setModified(false);
      onEditChange(null);
      return;
    }
    if (price !== null && (price < 0 || Number.isNaN(price))) {
      setEditError('Unit price must be zero or more (or empty for the catalog price).');
      setModified(false);
      onEditChange(null);
      return;
    }
    setEditError(null);
    const qtyChanged = !sameNumber(qty, num(origQty));
    const priceChanged = !sameNumber(price, num(origPrice === '' ? null : origPrice));
    if (!qtyChanged && !priceChanged) {
      setModified(false);
      onEditChange(null);
      return;
    }
    setModified(true);
    const edited: Record<string, unknown> = { ...args, quantity: nextQty.trim() };
    if (nextPrice.trim() === '') {
      delete edited.unit_price;
    } else {
      edited.unit_price = nextPrice.trim();
    }
    onEditChange(edited);
  };

  const effective: Record<string, unknown> = modified
    ? { ...args, quantity: qtyDraft, ...(priceDraft.trim() === '' ? { unit_price: undefined } : { unit_price: priceDraft }) }
    : args;
  const effQty = num(effective.quantity);
  const effPrice = num(effective.unit_price);
  const total = effQty !== null && effPrice !== null ? effQty * effPrice : null;

  const rows: Array<[string, string]> = [
    ['Customer', str(args.customer_name) || 'Walk-in'],
    [
      'Product',
      `${str(args.product_name) || str(args.variant_sku) || '—'}${args.variant_sku && args.product_name ? ` (${str(args.variant_sku)})` : ''}`,
    ],
    ['Quantity', effQty !== null ? `${effQty}` : '—'],
    ['Unit price', effPrice !== null ? fmtMoney(effPrice) : 'Catalog price'],
    ['Total', total !== null ? fmtMoney(total) : 'Calculated from catalog price on approval'],
    ['Payment', paymentLabel(effective)],
  ];

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <Badge variant="warning" size="sm">create_sale</Badge>
        <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">Record this sale?</span>
        {modified && <Badge variant="warning" size="sm">Modified</Badge>}
      </div>
      <dl className="divide-y divide-zinc-100 dark:divide-zinc-800 rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 px-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 py-1.5 text-xs">
            <dt className="text-zinc-500 dark:text-zinc-400">{label}</dt>
            <dd className="font-medium text-zinc-900 dark:text-zinc-100 text-right">{value}</dd>
          </div>
        ))}
      </dl>
      {allowEdit && (
        <div className="grid grid-cols-2 gap-2">
          <Input
            label="Quantity"
            value={qtyDraft}
            disabled={disabled}
            inputMode="decimal"
            onChange={(e) => {
              setQtyDraft(e.target.value);
              pushEdit(e.target.value, priceDraft);
            }}
          />
          <Input
            label="Unit price (empty = catalog)"
            value={priceDraft}
            disabled={disabled}
            inputMode="decimal"
            placeholder="e.g. 850"
            onChange={(e) => {
              setPriceDraft(e.target.value);
              pushEdit(qtyDraft, e.target.value);
            }}
          />
        </div>
      )}
      {editError && <p className="text-xs text-rose-600 dark:text-rose-400">{editError}</p>}
      <details className="text-[11px] text-zinc-500 dark:text-zinc-400">
        <summary className="cursor-pointer select-none hover:text-zinc-700 dark:hover:text-zinc-300">
          Technical details
        </summary>
        <pre className="mt-1 overflow-x-auto font-mono leading-relaxed">
          {(() => {
            try {
              return JSON.stringify(args, null, 2);
            } catch {
              return String(args);
            }
          })()}
        </pre>
      </details>
    </div>
  );
}
