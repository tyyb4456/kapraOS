import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, Building2 } from 'lucide-react';
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

export function SuppliersPage() {
  const [searchTerm, setSearchTerm] = useState('');

  return (
    <PageContainer>
      <PageHeader
        title="Suppliers & Textile Mills"
        description="Maintain fabric suppliers, weaving mills, wholesale contacts, and payables."
        breadcrumbs={[
          { label: 'Relationships', href: '/suppliers' },
          { label: 'Suppliers' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/suppliers/khata">
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Building2 className="w-4 h-4" />}
              >
                Supplier Khata
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
            >
              Add Supplier
            </Button>
          </div>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <Input
            placeholder="Search suppliers by name or contact person..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            leftIcon={<Search className="w-4 h-4" />}
          />
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Supplier / Mill Name</TableHead>
            <TableHead>Contact Person</TableHead>
            <TableHead>Phone</TableHead>
            <TableHead align="right">Current Payable</TableHead>
            <TableHead>Status</TableHead>
            <TableHead align="right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Kohinoor Textile Mills Ltd.
            </TableCell>
            <TableCell className="text-zinc-700">Tariq Mahmood</TableCell>
            <TableCell className="font-mono text-xs text-zinc-600">
              042-3591234
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-zinc-900">
              {formatCurrency(185000)}
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Active Supplier
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Link to="/suppliers/khata">
                <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                  View Ledger
                </Button>
              </Link>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Nishat Weaving Mills
            </TableCell>
            <TableCell className="text-zinc-700">Mian Aslam</TableCell>
            <TableCell className="font-mono text-xs text-zinc-600">
              041-8765432
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-zinc-900">
              {formatCurrency(72000)}
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Active Supplier
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Link to="/suppliers/khata">
                <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                  View Ledger
                </Button>
              </Link>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
