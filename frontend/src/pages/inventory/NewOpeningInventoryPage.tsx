import { useMemo, useRef, useState, useEffect } from 'react';
import { ArrowLeft, AlertCircle, CheckCircle2, FileUp, Loader2, Plus, Search, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Select,
  Textarea,
} from '../../components/ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';
import { getProducts } from '../../lib/api/products.ts';
import { postOpeningInventory, previewOpeningInventory } from '../../lib/api/openingInventory.ts';
import type {
  OpeningInventoryCostStatus,
  OpeningInventoryLineInput,
  OpeningInventoryPreview,
  ProductVariant,
} from '../../types/index.ts';

interface VariantWithProduct extends ProductVariant {
  product_name: string;
  unit: string;
}

interface OpeningLineDraft {
  id: string;
  variant_id: string;
  product_name: string;
  sku: string;
  unit: string;
  quantity: string;
  unit_cost: string;
  cost_status: OpeningInventoryCostStatus;
}

// Minimal RFC-4180-ish CSV reader: quoted fields with "" escapes, comma
// separators, CRLF or LF line endings. Enough for exported stock sheets.
function parseCsvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (ch !== '\r') {
      field += ch;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((cell) => cell.trim() !== ''));
}

export function NewOpeningInventoryPage() {
  const navigate = useNavigate();
  const [variants, setVariants] = useState<VariantWithProduct[]>([]);
  const [search, setSearch] = useState('');
  const [items, setItems] = useState<OpeningLineDraft[]>([]);
  const [notes, setNotes] = useState('');
  const [usedCsv, setUsedCsv] = useState(false);
  const [importErrors, setImportErrors] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<OpeningInventoryPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [posting, setPosting] = useState(false);
  // Stable across retries of the same basket: a retried post returns the
  // already-committed batch instead of creating a second one.
  const idempotencyKeyRef = useRef<string | null>(null);
  if (idempotencyKeyRef.current === null) {
    idempotencyKeyRef.current = crypto.randomUUID();
  }

  useEffect(() => {
    const loadVariants = async () => {
      try {
        const products = await getProducts();
        setVariants(
          products.flatMap((product) =>
            (product.variants || []).map((variant) => ({
              ...variant,
              product_name: product.name,
              unit: variant.unit || product.unit,
            })),
          ),
        );
      } catch (err) {
        console.error('Failed to load catalog:', err);
      }
    };
    void loadVariants();
  }, []);

  const matchingVariants = useMemo(() => {
    const term = search.trim().toLowerCase();
    const active = variants.filter((variant) => variant.is_active !== false);
    if (!term) return active.slice(0, 12);
    return active
      .filter(
        (variant) =>
          variant.sku.toLowerCase().includes(term) ||
          variant.product_name.toLowerCase().includes(term) ||
          (variant.barcode || '').toLowerCase().includes(term),
      )
      .slice(0, 12);
  }, [variants, search]);

  const invalidatePreview = () => setPreview(null);

  const addVariant = (variant: VariantWithProduct) => {
    if (items.some((item) => item.variant_id === variant.id)) {
      setError(`${variant.sku} is already in the list`);
      return;
    }
    setError(null);
    setItems([
      ...items,
      {
        id: crypto.randomUUID(),
        variant_id: variant.id,
        product_name: variant.product_name,
        sku: variant.sku,
        unit: variant.unit,
        quantity: '',
        unit_cost: '',
        cost_status: 'known',
      },
    ]);
    invalidatePreview();
  };

  const updateItem = (id: string, changes: Partial<OpeningLineDraft>) => {
    setItems(items.map((item) => (item.id === id ? { ...item, ...changes } : item)));
    invalidatePreview();
  };

  const removeItem = (id: string) => {
    setItems(items.filter((item) => item.id !== id));
    invalidatePreview();
  };

  const handleImportCsv = async (file: File) => {
    setImportErrors([]);
    setError(null);
    const rows = parseCsvRows(await file.text());
    if (rows.length === 0) {
      setImportErrors(['The file is empty.']);
      return;
    }

    const header = rows[0].map((cell) => cell.trim().toLowerCase());
    const headerNames = ['sku', 'barcode', 'quantity', 'qty', 'unit_cost', 'cost', 'cost_price', 'purchase_price', 'cost_status', 'status'];
    const hasHeader = header.some((cell) => headerNames.includes(cell));
    const findColumn = (...names: string[]) => header.findIndex((cell) => names.includes(cell));
    const columns = hasHeader
      ? {
          sku: findColumn('sku'),
          barcode: findColumn('barcode'),
          quantity: findColumn('quantity', 'qty'),
          unit_cost: findColumn('unit_cost', 'cost', 'cost_price', 'purchase_price'),
          cost_status: findColumn('cost_status', 'status'),
        }
      : { sku: 0, barcode: -1, quantity: 1, unit_cost: 2, cost_status: 3 };
    const dataRows = hasHeader ? rows.slice(1) : rows;

    const lookup = new Map<string, VariantWithProduct>();
    for (const variant of variants) {
      lookup.set(variant.sku.trim().toLowerCase(), variant);
      if (variant.barcode) lookup.set(variant.barcode.trim().toLowerCase(), variant);
    }

    const newItems: OpeningLineDraft[] = [];
    const problems: string[] = [];
    dataRows.forEach((cells, rowIndex) => {
      const lineNumber = rowIndex + (hasHeader ? 2 : 1);
      const sku = columns.sku >= 0 ? (cells[columns.sku] || '').trim() : '';
      const barcode = columns.barcode >= 0 ? (cells[columns.barcode] || '').trim() : '';
      const identifier = sku || barcode;
      if (!identifier) {
        problems.push(`Line ${lineNumber}: missing SKU/barcode, skipped`);
        return;
      }
      const variant = lookup.get(identifier.toLowerCase());
      if (!variant) {
        problems.push(`Line ${lineNumber}: no variant matches "${identifier}", skipped`);
        return;
      }
      const quantity = columns.quantity >= 0 ? (cells[columns.quantity] || '').trim() : '';
      if (!quantity) {
        problems.push(`Line ${lineNumber} (${identifier}): missing quantity, skipped`);
        return;
      }
      if (items.some((item) => item.variant_id === variant.id) || newItems.some((item) => item.variant_id === variant.id)) {
        problems.push(`Line ${lineNumber} (${identifier}): already in the list, skipped`);
        return;
      }
      const rawCost = columns.unit_cost >= 0 ? (cells[columns.unit_cost] || '').trim() : '';
      const rawStatus = columns.cost_status >= 0 ? (cells[columns.cost_status] || '').trim().toLowerCase() : '';
      newItems.push({
        id: crypto.randomUUID(),
        variant_id: variant.id,
        product_name: variant.product_name,
        sku: variant.sku,
        unit: variant.unit,
        quantity,
        unit_cost: rawCost !== '' ? rawCost : String(variant.purchase_price ?? ''),
        cost_status: rawStatus === 'estimated' ? 'estimated' : 'known',
      });
    });

    if (newItems.length > 0) {
      setItems((current) => [...current, ...newItems]);
      setUsedCsv(true);
      invalidatePreview();
    }
    setImportErrors(problems);
  };

  const buildLines = (): OpeningInventoryLineInput[] =>
    items.map((item) => ({
      variant_id: item.variant_id,
      quantity: item.quantity.trim() === '' ? null : item.quantity.trim(),
      unit_cost: item.unit_cost.trim() === '' ? null : item.unit_cost.trim(),
      cost_status: item.cost_status,
    }));

  const handlePreview = async () => {
    if (items.length === 0) {
      setError('Add at least one variant before previewing.');
      return;
    }
    setPreviewing(true);
    setError(null);
    try {
      setPreview(await previewOpeningInventory(buildLines()));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  };

  const handlePost = async () => {
    if (!preview?.valid) {
      setError('Preview the batch and resolve every line error before posting.');
      return;
    }
    setPosting(true);
    setError(null);
    try {
      const activeKey = idempotencyKeyRef.current ?? crypto.randomUUID();
      idempotencyKeyRef.current = activeKey;
      const batch = await postOpeningInventory({
        idempotency_key: activeKey,
        lines: buildLines(),
        source: usedCsv ? 'import' : 'manual',
        notes: notes.trim() === '' ? null : notes.trim(),
      });
      // The basket is committed; a future new batch gets a fresh key.
      idempotencyKeyRef.current = crypto.randomUUID();
      navigate(`/inventory/opening/${batch.id}`, { state: { justPosted: true } });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post opening inventory');
    } finally {
      setPosting(false);
    }
  };

  const lineValue = (item: OpeningLineDraft): number => {
    const quantity = Number.parseFloat(item.quantity);
    const cost = Number.parseFloat(item.unit_cost);
    if (!Number.isFinite(quantity) || !Number.isFinite(cost)) return 0;
    return quantity * cost;
  };

  return (
    <PageContainer>
      <div className="flex items-center gap-2 mb-2">
        <Link
          to="/inventory/opening"
          className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Opening Inventory
        </Link>
      </div>

      <PageHeader
        title="Record Opening Stock"
        description="The stock already on your shelves when you started using KapraOS. This is not a purchase: there is no supplier and no payable."
      />

      {error && (
        <Alert variant="danger" className="mb-4" title="Could not continue">
          {error}
        </Alert>
      )}

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Pick Variants</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2">
              <Input
                placeholder="Search by SKU, barcode or product name..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
              <div className="mt-3 flex flex-wrap gap-2">
                {matchingVariants.length === 0 ? (
                  <p className="text-xs text-zinc-500">No matching variants.</p>
                ) : (
                  matchingVariants.map((variant) => (
                    <button
                      key={variant.id}
                      type="button"
                      onClick={() => addVariant(variant)}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 rounded-md border border-zinc-200 dark:border-zinc-700 transition-colors"
                      title={`${variant.product_name} (${variant.sku})`}
                    >
                      <Plus className="w-3 h-3" />
                      <span className="font-mono">{variant.sku}</span>
                      <span className="text-zinc-500 max-w-[140px] truncate">{variant.product_name}</span>
                    </button>
                  ))
                )}
              </div>
            </div>

            <div className="clay-panel-soft rounded-xl border border-dashed border-[var(--clay-border-soft)] p-3">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-2">
                Import from CSV
              </h4>
              <label className="flex flex-col items-center justify-center gap-2 cursor-pointer rounded-lg border border-dashed border-zinc-300 dark:border-zinc-600 py-4 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 transition-colors">
                <FileUp className="w-4 h-4 text-zinc-500" />
                <span className="text-xs text-zinc-600 dark:text-zinc-300">Choose a .csv file</span>
                <input
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void handleImportCsv(file);
                    e.target.value = '';
                  }}
                />
              </label>
              <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
                Columns: <span className="font-mono">sku</span> (or{' '}
                <span className="font-mono">barcode</span>),{' '}
                <span className="font-mono">quantity</span>,{' '}
                <span className="font-mono">unit_cost</span>,{' '}
                <span className="font-mono">cost_status</span>. Blank costs fall back to
                the variant&apos;s purchase price. Only CSV is supported.
              </p>
            </div>
          </div>

          {importErrors.length > 0 && (
            <Alert variant="warning" title="Some CSV rows were skipped">
              <ul className="list-disc pl-4 space-y-0.5">
                {importErrors.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-semibold">
            Opening Lines ({items.length})
          </CardTitle>
          {preview && (
            <Badge variant={preview.valid ? 'success' : 'danger'} size="sm">
              {preview.valid ? 'Ready to post' : 'Has line errors'}
            </Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {items.length === 0 ? (
            <p className="py-8 text-center text-xs text-zinc-500">
              No lines yet. Pick variants above or import a CSV file.
            </p>
          ) : (
            items.map((item, index) => {
              const line = preview?.lines[index];
              return (
                <div
                  key={item.id}
                  className="rounded-xl border border-zinc-100 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/40 p-3"
                >
                  <div className="grid grid-cols-1 sm:grid-cols-12 gap-3 items-end">
                    <div className="sm:col-span-4">
                      <div className="font-medium text-sm text-zinc-900 dark:text-zinc-100">
                        {item.product_name}
                      </div>
                      <div className="text-xs text-zinc-500 font-mono">
                        {item.sku} · {item.unit}
                      </div>
                      {line && (
                        <div className="mt-1 text-[11px] text-zinc-500">
                          In stock now: {String(line.current_quantity)} ·{' '}
                          {line.projected_weighted_average_cost !== null
                            ? `new avg cost ${formatCurrency(Number(line.projected_weighted_average_cost))}`
                            : 'average cost unavailable'}
                        </div>
                      )}
                    </div>
                    <div className="sm:col-span-2">
                      <label className="block text-[11px] font-medium text-zinc-600 dark:text-zinc-300 mb-1">
                        Quantity
                      </label>
                      <Input
                        type="number"
                        min="0"
                        step="0.001"
                        placeholder="0"
                        value={item.quantity}
                        onChange={(e) => updateItem(item.id, { quantity: e.target.value })}
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label className="block text-[11px] font-medium text-zinc-600 dark:text-zinc-300 mb-1">
                        Unit Cost (PKR)
                      </label>
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        placeholder="0.00"
                        value={item.unit_cost}
                        onChange={(e) => updateItem(item.id, { unit_cost: e.target.value })}
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label className="block text-[11px] font-medium text-zinc-600 dark:text-zinc-300 mb-1">
                        Cost Status
                      </label>
                      <Select
                        value={item.cost_status}
                        onChange={(e) =>
                          updateItem(item.id, {
                            cost_status: e.target.value as OpeningInventoryCostStatus,
                          })
                        }
                      >
                        <option value="known">Known (from records)</option>
                        <option value="estimated">Estimated</option>
                      </Select>
                    </div>
                    <div className="sm:col-span-2 flex items-center justify-end gap-2">
                      <div className="text-right">
                        <div className="text-[11px] text-zinc-500">Line value</div>
                        <div className="font-tabular font-semibold text-sm text-zinc-900 dark:text-zinc-100">
                          {formatCurrency(lineValue(item))}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeItem(item.id)}
                        className="text-zinc-400 hover:text-rose-600 p-1 rounded"
                        title="Remove line"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {line && line.errors.length > 0 && (
                    <ul className="mt-2 space-y-0.5 text-[11px] text-rose-600 dark:text-rose-400">
                      {line.errors.map((message) => (
                        <li key={message} className="flex items-start gap-1">
                          <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
                          {message}
                        </li>
                      ))}
                    </ul>
                  )}
                  {line && line.errors.length === 0 && line.warnings.length > 0 && (
                    <ul className="mt-2 space-y-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                      {line.warnings.map((message) => (
                        <li key={message}>Note: {message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="space-y-4 p-4">
          <Textarea
            label="Notes (optional)"
            placeholder="e.g. Physical count taken at shop opening on 18 Oct 2026."
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
          />

          {preview && (
            <div className="rounded-xl border border-[var(--clay-border-soft)] bg-[var(--clay-surface-soft)] p-3">
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <span className="inline-flex items-center gap-1 font-medium text-zinc-700 dark:text-zinc-200">
                  {preview.valid ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                  ) : (
                    <AlertCircle className="w-3.5 h-3.5 text-rose-500" />
                  )}
                  {preview.valid ? 'Batch is valid' : 'Batch has errors'}
                </span>
                <span className="text-zinc-500">
                  {preview.entry_count} line(s) · {preview.estimated_entry_count} estimated
                </span>
                <span className="ml-auto font-tabular font-semibold text-zinc-900 dark:text-zinc-100">
                  Total {formatCurrency(Number(preview.total_value))}
                </span>
              </div>
            </div>
          )}

          <div className="flex flex-col sm:flex-row items-center justify-end gap-2 border-t border-zinc-100 dark:border-zinc-800 pt-4">
            <Link to="/inventory/opening">
              <Button type="button" variant="outline" size="sm">
                Cancel
              </Button>
            </Link>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handlePreview()}
              disabled={previewing || posting || items.length === 0}
              leftIcon={previewing ? <Loader2 className="w-4 h-4 animate-spin" /> : undefined}
            >
              {previewing ? 'Checking...' : 'Preview Batch'}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => void handlePost()}
              disabled={posting || !preview?.valid}
              leftIcon={posting ? <Loader2 className="w-4 h-4 animate-spin" /> : undefined}
            >
              {posting ? 'Posting...' : 'Post Opening Inventory'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
