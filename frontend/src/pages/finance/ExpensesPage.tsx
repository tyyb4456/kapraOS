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
import { formatCurrency, formatDate } from '../../lib/formatters.ts';

const SAMPLE_DATE_1 = '2026-03-17T11:00:00.000Z';
const SAMPLE_DATE_2 = '2026-03-16T16:00:00.000Z';

export function ExpensesPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);

  return (
    <PageContainer>
      <PageHeader
        title="Shop Operating Expenses"
        description="Log everyday store overheads: shop rent, electricity, staff chai/meals, packaging, and logistics."
        breadcrumbs={[
          { label: 'Finance', href: '/expenses' },
          { label: 'Expenses' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus className="w-4 h-4" />}
            onClick={() => setIsAddModalOpen(true)}
          >
            Record Expense
          </Button>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search by description or receipt reference..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-48">
              <Select defaultValue="all">
                <option value="all">All Categories</option>
                <option value="utilities">Electricity & Utilities</option>
                <option value="refreshment">Tea / Staff Meals</option>
                <option value="packaging">Bags & Packaging</option>
                <option value="rent">Shop Rent</option>
                <option value="transport">Freight / Transport</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            <TableHead>Expense Category</TableHead>
            <TableHead>Description</TableHead>
            <TableHead>Payment Method</TableHead>
            <TableHead align="right">Amount</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_1, true)}
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Tea & Refreshments
              </Badge>
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Staff chai and guest tea expenses
            </TableCell>
            <TableCell className="capitalize text-zinc-600">Cash</TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(850)}
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_2, false)}
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Bags & Packaging
              </Badge>
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Custom printed cloth shopping bags (500 pcs)
            </TableCell>
            <TableCell className="capitalize text-zinc-600">Cash</TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(12500)}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>

      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Record Store Expense"
        description="Log operational costs to accurately reflect net profit."
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
              Save Expense
            </Button>
          </>
        }
      >
        <div className="space-y-3.5">
          <Select label="Expense Category" defaultValue="utilities">
            <option value="utilities">Electricity & Utilities</option>
            <option value="refreshment">Tea / Staff Meals</option>
            <option value="packaging">Bags & Packaging</option>
            <option value="rent">Shop Rent</option>
            <option value="transport">Freight / Transport</option>
          </Select>
          <Input label="Amount (PKR)" placeholder="0.00" type="number" />
          <Input label="Description" placeholder="e.g. Monthly electricity bill for shop #4" />
          <Select label="Payment Source" defaultValue="cash">
            <option value="cash">Shop Cash Drawer</option>
            <option value="bank">Bank Account</option>
          </Select>
        </div>
      </Dialog>
    </PageContainer>
  );
}
