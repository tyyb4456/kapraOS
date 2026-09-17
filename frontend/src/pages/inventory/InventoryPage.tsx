import { useState } from 'react';
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
} from '../../components/ui/index.ts';
import { formatCurrency, formatQuantity } from '../../lib/formatters.ts';

export function InventoryPage() {
  const [searchTerm, setSearchTerm] = useState('');

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
          <TableRow>
            <TableCell className="font-mono text-xs text-zinc-500">
              COT-LAWN-01-NAVY
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Egyptian Lawn - Plain
            </TableCell>
            <TableCell className="text-xs text-zinc-600">
              Color: Navy Blue
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatQuantity(145.5, 'meters')}
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(145.5 * 650)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(145.5 * 950)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Healthy
              </Badge>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-mono text-xs text-zinc-500">
              SUIT-WOL-04-BLK
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Italian Wool Blend 120s
            </TableCell>
            <TableCell className="text-xs text-zinc-600">
              Color: Jet Black
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-amber-700">
              {formatQuantity(8.25, 'meters')}
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(8.25 * 2200)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(8.25 * 3400)}
            </TableCell>
            <TableCell>
              <Badge variant="warning" size="sm">
                Low Stock
              </Badge>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
