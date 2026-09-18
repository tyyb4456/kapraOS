import { useState, useEffect } from 'react';
import { Search } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Input,
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
import { formatCurrency, formatQuantity } from '../../lib/formatters.ts';
import { getInventory } from '../../lib/api/inventory.ts';
import type { InventoryItem } from '../../types/index.ts';

export function InventoryPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadInventory = async () => {
      try {
        setLoading(true);
        const data = await getInventory();
        setInventory(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load inventory');
      } finally {
        setLoading(false);
      }
    };
    loadInventory();
  }, []);

  const filteredInventory = inventory.filter(
    (item) =>
      item.sku.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.product_name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getStockStatusBadge = (qty: number, threshold?: number) => {
    if (qty <= 0) {
      return <Badge variant="destructive" size="sm">Out of Stock</Badge>;
    }
    if (threshold && qty <= threshold) {
      return <Badge variant="warning" size="sm">Low Stock</Badge>;
    }
    return <Badge variant="success" size="sm">Healthy</Badge>;
  };

  return (
    <PageContainer>
      <PageHeader
        title="Stock & Yardage Inventory"
        description="Monitor physical cloth rolls, meters on hand, piece quantities, and reorder levels."
        breadcrumbs={[
          { label: 'Inventory', href: '/inventory' },
          { label: 'Stock Levels' },
        ]}
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Filter inventory by SKU or product name..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-44">
              <Select defaultValue="all">
                <option value="all">All Stock Status</option>
                <option value="low">Low Stock Only</option>
                <option value="out">Out of Stock</option>
                <option value="in">Sufficient Stock</option>
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
              <TableHead>Variant SKU</TableHead>
              <TableHead>Fabric / Item Name</TableHead>
              <TableHead>Color / Specs</TableHead>
              <TableHead align="right">Qty On Hand</TableHead>
              <TableHead align="right">Cost Value</TableHead>
              <TableHead align="right">Retail Value</TableHead>
              <TableHead>Stock Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                </TableRow>
              ))
            ) : filteredInventory.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching inventory items found' : 'No inventory items yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredInventory.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="font-mono text-xs text-zinc-500">
                    {item.sku}
                  </TableCell>
                  <TableCell className="font-medium text-zinc-900">
                    {item.product_name}
                  </TableCell>
                  <TableCell className="text-xs text-zinc-600">
                    {Object.entries(item.attributes)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(', ') || '—'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                    {formatQuantity(item.quantity_on_hand, item.unit)}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-600">
                    {formatCurrency(item.quantity_on_hand * item.cost_price)}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                    {formatCurrency(item.quantity_on_hand * item.selling_price)}
                  </TableCell>
                  <TableCell>
                    {getStockStatusBadge(item.quantity_on_hand, item.low_stock_threshold)}
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