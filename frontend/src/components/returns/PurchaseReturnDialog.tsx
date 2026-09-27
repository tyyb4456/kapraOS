import { useEffect, useMemo, useState } from 'react';
import { Button, Dialog, Input } from '../ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';
import {
  createPurchaseReturn,
  getPurchaseDetail,
  getPurchaseReturns,
} from '../../lib/api/returns.ts';
import type { Purchase, PurchaseDetailItem } from '../../types/index.ts';

interface ReturnLineState {
  purchase_item_id: string;
  variant_id: string;
  quantity: string;
  unit_cost: number;
  original_qty: number;
  remaining_qty: number;
}

interface PurchaseReturnDialogProps {
  purchase: Purchase | null;
  onClose: () => void;
  onSuccess: () => void;
}

function toNumber(value: number | string | undefined | null): number {
  if (value === undefined || value === null) return 0;
  const n = typeof value === 'string' ? parseFloat(value) : value;
  return isNaN(n) ? 0 : n;
}

export function PurchaseReturnDialog({ purchase, onClose, onSuccess }: PurchaseReturnDialogProps) {
  const [lines, setLines] = useState<ReturnLineState[]>([]);
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultTotal, setResultTotal] = useState<number | null>(null);

  useEffect(() => {
    if (!purchase) {
      setLines([]);
      setNotes('');
      setError(null);
      setResultTotal(null);
      return;
    }
    const load = async () => {
      setLoading(true);
      setError(null);
      setResultTotal(null);
      try {
        const [detail, returns] = await Promise.all([
          getPurchaseDetail(purchase.id),
          getPurchaseReturns(purchase.id).catch(() => []),
        ]);
        const returnedByItem = new Map<string, number>();
        for (const r of returns) {
          for (const item of r.items ?? []) {
            const prev = returnedByItem.get(item.purchase_item_id) ?? 0;
            returnedByItem.set(item.purchase_item_id, prev + toNumber(item.quantity));
          }
        }
        const items: PurchaseDetailItem[] = detail.items ?? [];
        setLines(
          items.map((item) => {
            const original = toNumber(item.quantity);
            const already = returnedByItem.get(item.id) ?? 0;
            const remaining = Math.max(0, original - already);
            return {
              purchase_item_id: item.id,
              variant_id: item.variant_id,
              quantity: '',
              unit_cost: toNumber(item.unit_cost),
              original_qty: original,
              remaining_qty: remaining,
            };
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load purchase details');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [purchase]);

  const estimatedTotal = useMemo(() => {
    return lines.reduce((sum, line) => {
      const qty = parseFloat(line.quantity);
      if (isNaN(qty) || qty <= 0) return sum;
      return sum + qty * line.unit_cost;
    }, 0);
  }, [lines]);

  const hasAnyQty = lines.some((l) => {
    const qty = parseFloat(l.quantity);
    return !isNaN(qty) && qty > 0;
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!purchase || submitting) return;
    setError(null);
    setResultTotal(null);
    const payloadLines = [];
    for (const line of lines) {
      if (!line.quantity.trim()) continue;
      const qty = parseFloat(line.quantity);
      if (isNaN(qty) || qty <= 0) {
        setError('Enter a valid quantity greater than 0 for each line you want to return.');
        return;
      }
      if (qty - line.remaining_qty > 1e-9) {
        setError(`Return quantity ${qty} exceeds remaining ${line.remaining_qty} for one of the items.`);
        return;
      }
      payloadLines.push({ purchase_item_id: line.purchase_item_id, quantity: qty });
    }
    if (payloadLines.length === 0) {
      setError('Enter at least one return quantity.');
      return;
    }
    setSubmitting(true);
    try {
      const response = await createPurchaseReturn(purchase.id, {
        lines: payloadLines,
        notes: notes.trim() || undefined,
      });
      setResultTotal(toNumber(response.total_return_amount));
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create supplier return');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={purchase !== null}
      onClose={onClose}
      title={purchase ? `Supplier Return — ${purchase.order_number || purchase.id.slice(0, 8)}` : 'Supplier Return'}
      description="Select consignments going back to the mill. Cost comes from the original bill — the backend reduces stock, payable and inventory value. Blocked when stock on hand is insufficient."
      maxWidth="lg"
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={submitting}>
            {resultTotal !== null ? 'Close' : 'Cancel'}
          </Button>
          {resultTotal === null && (
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
      ) : resultTotal !== null ? (
        <div className="space-y-2 text-sm">
          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-md text-emerald-800">
            Supplier return saved. Stock, payable and inventory value are updated.
          </div>
          <div className="flex justify-between text-zinc-700">
            <span>Payable reduction</span>
            <span className="font-tabular font-semibold">{formatCurrency(resultTotal)}</span>
          </div>
          <p className="text-[11px] text-zinc-500">
            No supplier cash refund is modelled — an over-return shows as supplier credit (negative payable).
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
            <p className="text-xs text-zinc-500">No bill lines found for this purchase.</p>
          ) : (
            <div className="space-y-2">
              {lines.map((line, idx) => (
                <div
                  key={line.purchase_item_id}
                  className="flex items-center gap-2 p-2 border border-zinc-200 rounded-md"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-zinc-900 truncate">
                      Line {idx + 1} · {formatCurrency(line.unit_cost)} / unit
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
            <span>Estimated payable reduction (qty × bill cost, before discount split)</span>
            <span className="font-tabular font-semibold">{formatCurrency(estimatedTotal)}</span>
          </div>
          <Input
            label="Notes (optional)"
            placeholder="e.g. 3 suits short-delivered, 2m defective"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
          <p className="text-[11px] text-zinc-500">
            Bills with returns can no longer be edited or voided. Returns need enough stock on hand.
          </p>
        </form>
      )}
    </Dialog>
  );
}
