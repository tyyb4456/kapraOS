import { useState } from 'react';
import { Link } from 'react-router-dom';
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
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';

const SAMPLE_DATE_1 = '2026-03-17T11:30:00.000Z';
const SAMPLE_DATE_2 = '2026-03-14T09:15:00.000Z';

export function PurchasesPage() {
  const [searchTerm, setSearchTerm] = useState('');

  return (
    <PageContainer>
      <PageHeader
        title="Purchasing & Supplier Orders"
        description="Track inward cloth roll consignments, mill purchases, and supplier shipments."
        breadcrumbs={[
          { label: 'Purchasing', href: '/purchases' },
          { label: 'Orders' },
        ]}
        actions={
          <Link to="/purchases/new">
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
            >
              New Purchase
            </Button>
          </Link>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search by order number or supplier..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-44">
              <Select defaultValue="all">
                <option value="all">All Order Status</option>
                <option value="received">Received / Inwarded</option>
                <option value="pending">Pending Delivery</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Order #</TableHead>
            <TableHead>Date</TableHead>
            <TableHead>Supplier Mill</TableHead>
            <TableHead align="right">Items Inward</TableHead>
            <TableHead align="right">Total Cost</TableHead>
            <TableHead>Status</TableHead>
            <TableHead align="right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="font-mono text-xs font-medium text-zinc-900">
              PO-2026-0042
            </TableCell>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_1, false)}
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Kohinoor Textile Mills Ltd.
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-700">
              12 rolls (600m)
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(390000)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Received
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Details
              </Button>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-mono text-xs font-medium text-zinc-900">
              PO-2026-0041
            </TableCell>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_2, false)}
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Nishat Weaving Mills
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-700">
              8 rolls (400m)
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(260000)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Received
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Details
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
