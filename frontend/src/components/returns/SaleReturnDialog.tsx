import { useEffect, useMemo, useState } from 'react';
import { Button, Dialog, Input } from '../ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';
import {
  createSaleReturn,
  getSaleDetail,
  getSaleReturns,
} from '../../lib/api/returns.ts';
import type { Sale, SaleDetailItem } from '../../types/index.ts';

interface ReturnLineState {
  sale_item_id: string;
  variant_id: string;
  quantity: string;
  unit_price: number;
  original_qty: number;
  remaining_qty: number;
}

interface SaleReturnDialogProps {
  sale: Sale | null;
  onClose: () => void;
  onSuccess: () => void;
}

function toNumber(value: number | string | undefined | null): number {
  if (value === undefined || value === null) return 0;
  const n = typeof value === 'string' ? parseFloat(value) : value;
  return isNaN(n) ? 0 : n;
}

export function SaleReturnDialog({ sale, onClose, onSuccess }: SaleReturnDialogProps) {
  const [lines, setLines] = useState<ReturnLineState[]>([]);
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    total: number;
    ar: number;
    cash: number;
  } | null>(null);

  useEffect(() => {
    if (!sale) {
      setLines([]);
      setNotes('');
      setError(null);
      setResult(null);
      return;
    }
    const load = async () => {
      setLoading(true);
      setError(null);
      setResult(null);
      try {
        const [detail, returns] = await Promise.all([
          getSaleDetail(sale.id),
          getSaleReturns(sale.id).catch(() => []),
        ]);
        const returnedByItem = new Map<string, number>();
        for (const r of returns) {
          for (const item of r.items ?? []) {
            const prev = returnedByItem.get(item.sale_item_id) ?? 0;
            returnedByItem.set(item.sale_item_id, prev + toNumber(item.quantity));
          }
        }
        const items: SaleDetailItem[] = detail.items ?? [];
        setLines(
          items.map((item) => {
            const original = toNumber(item.quantity);
            const already = returnedByItem.get(item.id) ?? 0;
            const remaining = Math.max(0, original - already);
            return {
              sale_item_id: item.id,
              variant_id: item.variant_id,
              quantity: '',
              unit_price: toNumber(item.unit_price),
              original_qty: original,
              remaining_qty: remaining,
            };
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load sale details');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [sale]);

  const estimatedTotal = useMemo(() => {
    return lines.reduce((sum, line) => {
      const qty = parseFloat(line.quantity);
      if (isNaN(qty) || qty <= 0) return sum;
      return sum + qty * line.unit_price;
    }, 0);
  }, [lines]);

  const hasAnyQty = lines.some((l) => {
    const qty = parseFloat(l.quantity);
    return !isNaN(qty) && qty > 0;
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!sale || submitting) return;
    setError(null);
    setResult(null);
    const payloadLines = [];
    for (const line of lines) {
      if (!line.quantity.trim()) continue;
      const qty = parseFloat(line.quantity);
      if (isNaN(qty) || qty <= 0) {
        setError(`Enter a valid quantity greater than 0 for each line you want to return.`);
        return;
      }
      if (qty - line.remaining_qty > 1e-9) {
        setError(
          `Return quantity ${qty} exceeds remaining ${line.remaining_qty} for one of the items.`,
        );
        return;
      }
      payloadLines.push({ sale_item_id: line.sale_item_id, quantity: qty });
    }
    if (payloadLines.length === 0) {
      setError('Enter at least one return quantity.');
      return;
    }
    setSubmitting(true);
    try {
      const response = await createSaleReturn(sale.id, {
        lines: payloadLines,
        notes: notes.trim() || undefined,
      });
      setResult({
        total: toNumber(response.total_return_amount),
        ar: toNumber(response.ar_amount),
        cash: toNumber(response.cash_refund),
      });
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create return');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={sale !== null}
      onClose={onClose}
      title={sale ? `Customer Return — ${sale.invoice_number || sale.id.slice(0, 8)}` : 'Customer Return'}
      description="Select items and quantities the customer is handing back. Prices come from the original bill — the backend calculates the refund, Khata reduction and stock put-back."
      maxWidth="lg"
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={submitting}>
            {result ? 'Close' : 'Cancel'}
          </Button>
          {!result && (
            <Button
              variant="primary"
              size="sm"
              onClick={handleSubmit}
              disabled={submitting || loading || !hasAnyQty}
            >
              {submitting ? 'Saving return...' : 'Save Return'}
            </Button>
          )}
        </>
      }
    >
      {loading ? (
        <p className="text-xs text-zinc-500">Loading bill lines and already-returned quantities…</p>
      ) : result ? (
        <div className="space-y-2 text-sm">
          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-md text-emerald-800">
            Return saved. Stock is back on hand and the Khata / ledger are updated.
          </div>
          <div className="flex justify-between text-zinc-700">
            <span>Total refund value</span>
            <span className="font-tabular font-semibold">{formatCurrency(result.total)}</span>
          </div>
          <div className="flex justify-between text-zinc-700">
            <span>Khata reduction (AR)</span>
            <span className="font-tabular">{formatCurrency(result.ar)}</span>
          </div>
          <div className="flex justify-between text-zinc-700">
            <span>Cash handed back</span>
            <span className="font-tabular">{formatCurrency(result.cash)}</span>
          </div>
          <p className="text-[11px] text-zinc-500">
            Cash refunds are ledger-only. No payment rows are created or edited.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-3.5">
          {error && (
            <div className="p-2.5 bg-rose-50 border border-rose-200 rounded text-xs text-rose-700">
              {error}
            </div>
          )}
          {lines.length === 0 ? (
            <p className="text-xs text-zinc-500">No bill lines found for this sale.</p>
          ) : (
            <div className="space-y-2">
              {lines.map((line, idx) => (
                <div
                  key={line.sale_item_id}
                  className="flex items-center gap-2 p-2 border border-zinc-200 rounded-md"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-zinc-900 truncate">
                      Line {idx + 1} · {formatCurrency(line.unit_price)} / unit
                    </div>
                    <div className="text-[11px] text-zinc-500">
                      Billed {line.original_qty} · Returnable {line.remaining_qty}
                    </div>
                  </div>
                  <div className="w-28">
                    <Input
                      type="number"
                      step="any"
                      min="0"
                      max={line.remaining_qty}
                      placeholder="Qty"
                      value={line.quantity}
                      disabled={line.remaining_qty <= 0}
                      onChange={(e) => {
                        const next = [...lines];
                        next[idx] = { ...line, quantity: e.target.value };
                        setLines(next);
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="flex justify-between text-xs text-zinc-600">
            <span>Estimated refund (qty × bill price, before bill-discount split)</span>
            <span className="font-tabular font-semibold">{formatCurrency(estimatedTotal)}</span>
          </div>
          <Input
            label="Notes (optional)"
            placeholder="e.g. 2m black lawn, suit returned"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
          <p className="text-[11px] text-zinc-500">
            Fully-returned bills stay in history. Bills with returns can no longer be edited or voided.
          </p>
        </form>
      )}
    </Dialog>
  );
}
