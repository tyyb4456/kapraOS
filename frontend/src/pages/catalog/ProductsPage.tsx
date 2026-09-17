import { useState } from 'react';
import { Plus, Search } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
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
  Dialog,
} from '../../components/ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';

export function ProductsPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);

  return (
    <PageContainer>
      <PageHeader
        title="Products & Fabric Catalog"
        description="Manage fabrics, materials, suitings, unstitched collections, and apparel variants."
        breadcrumbs={[
          { label: 'Catalog', href: '/products' },
          { label: 'Products' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus className="w-4 h-4" />}
            onClick={() => setIsAddModalOpen(true)}
          >
            Add Product
          </Button>
        }
      />

      {/* Filter and Search Bar */}
      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search products by title, SKU, fabric type..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-48">
              <Select defaultValue="all">
                <option value="all">All Categories</option>
                <option value="unstitched">Unstitched Fabric</option>
                <option value="cotton">Cotton & Lawn</option>
                <option value="silk">Silk & Chiffon</option>
                <option value="suiting">Men's Suiting</option>
              </Select>
            </div>
            <div className="w-full sm:w-36">
              <Select defaultValue="all">
                <option value="all">All Units</option>
                <option value="meters">Meters</option>
                <option value="yards">Yards</option>
                <option value="pieces">Pieces</option>
                <option value="rolls">Rolls</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Products Table Shell */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Code / SKU</TableHead>
            <TableHead>Product Name</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Base Unit</TableHead>
            <TableHead align="right">Cost Price</TableHead>
            <TableHead align="right">Selling Price</TableHead>
            <TableHead>Status</TableHead>
            <TableHead align="right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {/* Sample Product Row for Structure Demonstration */}
          <TableRow>
            <TableCell className="font-mono text-xs text-zinc-500">
              COT-LAWN-01
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Premium Egyptian Lawn - Plain Dyed
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Cotton & Lawn
              </Badge>
            </TableCell>
            <TableCell className="capitalize text-zinc-600">meters</TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(650)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(950)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Active
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Edit
              </Button>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-mono text-xs text-zinc-500">
              SUIT-WOL-04
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Super 120s Italian Wool Blend
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Men's Suiting
              </Badge>
            </TableCell>
            <TableCell className="capitalize text-zinc-600">meters</TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(2200)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(3400)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Active
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Edit
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>

      {/* Add Product Modal Shell */}
      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add New Fabric Product"
        description="Define standard catalog metadata and measurement unit."
        footer={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsAddModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => setIsAddModalOpen(false)}
            >
              Save Product
            </Button>
          </>
        }
      >
        <div className="space-y-3.5">
          <Input label="Product Title" placeholder="e.g. Wash & Wear Soft Finish" />
          <div className="grid grid-cols-2 gap-3">
            <Input label="Product Code / SKU" placeholder="e.g. WNW-01" />
            <Select label="Measurement Unit" defaultValue="meters">
              <option value="meters">Meters</option>
              <option value="yards">Yards</option>
              <option value="pieces">Pieces</option>
              <option value="sets">Sets</option>
              <option value="rolls">Rolls</option>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Default Cost Price (PKR)" placeholder="0.00" type="number" />
            <Input label="Default Selling Price (PKR)" placeholder="0.00" type="number" />
          </div>
        </div>
      </Dialog>
    </PageContainer>
  );
}
