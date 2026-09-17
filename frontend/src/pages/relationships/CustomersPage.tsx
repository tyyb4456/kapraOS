import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, BookOpen } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
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
import { formatCurrency } from '../../lib/formatters.ts';

export function CustomersPage() {
  const [searchTerm, setSearchTerm] = useState('');

  return (
    <PageContainer>
      <PageHeader
        title="Customer Directory & Khata"
        description="Maintain retail buyers, wholesale clients, contact numbers, and active balances."
        breadcrumbs={[
          { label: 'Relationships', href: '/customers' },
          { label: 'Customers' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/customers/khata">
              <Button
                variant="outline"
                size="sm"
                leftIcon={<BookOpen className="w-4 h-4" />}
              >
                Khata Ledger
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
            >
              Add Customer
            </Button>
          </div>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <Input
            placeholder="Search customers by name or phone number..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            leftIcon={<Search className="w-4 h-4" />}
          />
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Customer Name</TableHead>
            <TableHead>Phone Number</TableHead>
            <TableHead align="right">Credit Limit</TableHead>
            <TableHead align="right">Current Balance (Khata)</TableHead>
            <TableHead>Status</TableHead>
            <TableHead align="right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Chaudhry Fabric Traders
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-600">
              0300-8472910
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(250000)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-rose-700">
              {formatCurrency(45000)}
            </TableCell>
            <TableCell>
              <Badge variant="warning" size="sm">
                Receivable
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Link to="/customers/khata">
                <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                  View Khata
                </Button>
              </Link>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Haji Muhammad & Sons
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-600">
              0321-4455667
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(500000)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-rose-700">
              {formatCurrency(112500)}
            </TableCell>
            <TableCell>
              <Badge variant="warning" size="sm">
                Receivable
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Link to="/customers/khata">
                <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                  View Khata
                </Button>
              </Link>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
