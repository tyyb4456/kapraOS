import { useState, useEffect } from 'react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Select,
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
  Skeleton,
} from '../../components/ui/index.ts';
import { formatDate, formatQuantity } from '../../lib/formatters.ts';
import { getStockMovements } from '../../lib/api/inventory.ts';
import type { StockMovement } from '../../types/index.ts';

type MovementType =
  | 'all'
  | 'purchase'
  | 'sale'
  | 'customer_return'
  | 'supplier_return'
  | 'adjustment'
  | 'damage'
  | 'transfer'
  | 'purchase_in'
  | 'sale_out'
  | 'return_in'
  | 'return_out';

export function StockMovementsPage() {
  const [filterType, setFilterType] = useState<MovementType>('all');
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadMovements = async () => {
      try {
        setLoading(true);
        const data = await getStockMovements({ limit: 200 });
        setMovements(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load stock movements');
      } finally {
        setLoading(false);
      }
    };
    loadMovements();
  }, []);

  const filteredMovements = movements.filter((m) => {
    if (filterType === 'all') return true;
    if (filterType === 'purchase' || filterType === 'purchase_in') {
      return m.movement_type === 'purchase' || m.movement_type === 'purchase_in';
    }
    if (filterType === 'sale' || filterType === 'sale_out') {
      return m.movement_type === 'sale' || m.movement_type === 'sale_out';
    }
    if (filterType === 'customer_return' || filterType === 'return_in') {
      return m.movement_type === 'customer_return' || m.movement_type === 'return_in';
    }
    if (filterType === 'supplier_return' || filterType === 'return_out') {
      return m.movement_type === 'supplier_return' || m.movement_type === 'return_out';
    }
    return m.movement_type === filterType;
  });

  const getMovementBadge = (type: string) => {
    switch (type) {
      case 'purchase':
      case 'purchase_in':
        return <Badge variant="success" size="sm">Purchase In</Badge>;
      case 'sale':
      case 'sale_out':
        return <Badge variant="neutral" size="sm">Sale Out</Badge>;
      case 'customer_return':
      case 'return_in':
        return <Badge variant="secondary" size="sm">Customer Return</Badge>;
      case 'supplier_return':
      case 'return_out':
        return <Badge variant="warning" size="sm">Supplier Return</Badge>;
      case 'adjustment':
        return <Badge variant="neutral" size="sm">Adjustment</Badge>;
      case 'damage':
        return <Badge variant="destructive" size="sm">Damage</Badge>;
      case 'transfer':
        return <Badge variant="secondary" size="sm">Transfer</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{type}</Badge>;
    }
  };

  const getQuantityColor = (type: string, quantity: number) => {
    if (type === 'purchase_in' || type === 'purchase' || type === 'return_in' || type === 'customer_return') {
      return 'text-emerald-700';
    }
    if (type === 'sale_out' || type === 'sale' || type === 'return_out' || type === 'supplier_return' || type === 'damage') {
      return 'text-rose-700';
    }
    return quantity >= 0 ? 'text-emerald-700' : 'text-rose-700';
  };

  return (
    <PageContainer>
      <PageHeader
        title="Stock Movements Ledger"
        description="Immutable audit trail of purchases, customer cuts, returns, and inventory adjustments."
        breadcrumbs={[
          { label: 'Inventory', href: '/inventory' },
          { label: 'Stock Movements' },
        ]}
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex items-center gap-3">
            <div className="w-full sm:w-56">
              <Select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value as MovementType)}
              >
                <option value="all">All Movement Types</option>
                <option value="purchase">Purchase Inward (+)</option>
                <option value="sale">POS Sale Cut (-)</option>
                <option value="customer_return">Customer Return (+)</option>
                <option value="supplier_return">Supplier Return (-)</option>
                <option value="adjustment">Stock Audit Adjustment</option>
                <option value="damage">Damage (-)</option>
                <option value="transfer">Transfer</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Timestamp</TableHead>
              <TableHead>SKU</TableHead>
              <TableHead>Product / Material</TableHead>
              <TableHead>Movement Type</TableHead>
              <TableHead align="right">Qty Delta</TableHead>
              <TableHead>Reference / Invoice</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                </TableRow>
              ))
            ) : filteredMovements.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                  No stock movements found
                </TableCell>
              </TableRow>
            ) : (
              filteredMovements.map((movement) => (
                <TableRow key={movement.id}>
                  <TableCell className="text-xs text-zinc-600">
                    {formatDate(movement.created_at, true)}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-zinc-500">
                    {movement.sku}
                  </TableCell>
                  <TableCell className="font-medium text-zinc-900">
                    {movement.product_name}
                  </TableCell>
                  <TableCell>{getMovementBadge(movement.movement_type)}</TableCell>
                  <TableCell align="right" className={`font-tabular font-semibold ${getQuantityColor(movement.movement_type, movement.quantity)}`}>
                    {movement.quantity >= 0 ? '+' : ''}{formatQuantity(movement.quantity, movement.unit)}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-zinc-600">
                    {movement.reference_type ? `${movement.reference_type}:${movement.reference_id}` : '—'}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>
    </PageContainer>
  );
}