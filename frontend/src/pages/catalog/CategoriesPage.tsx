import { useState } from 'react';
import { Plus } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
  Dialog,
} from '../../components/ui/index.ts';

export function CategoriesPage() {
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);

  return (
    <PageContainer>
      <PageHeader
        title="Product Categories"
        description="Organize fabrics, apparel, and stock items into business classifications."
        breadcrumbs={[
          { label: 'Catalog', href: '/products' },
          { label: 'Categories' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus className="w-4 h-4" />}
            onClick={() => setIsAddModalOpen(true)}
          >
            Add Category
          </Button>
        }
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category Name</TableHead>
            <TableHead>Code</TableHead>
            <TableHead>Parent Category</TableHead>
            <TableHead align="center">Products Count</TableHead>
            <TableHead align="right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Cotton & Lawn
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              CAT-COTTON
            </TableCell>
            <TableCell className="text-zinc-500">—</TableCell>
            <TableCell align="center">
              <Badge variant="neutral" size="sm">
                24 products
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Edit
              </Button>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Men's Suiting
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              CAT-SUITING
            </TableCell>
            <TableCell className="text-zinc-500">—</TableCell>
            <TableCell align="center">
              <Badge variant="neutral" size="sm">
                18 products
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Edit
              </Button>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-medium text-zinc-900">
              Silk & Chiffon
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              CAT-SILK
            </TableCell>
            <TableCell className="text-zinc-500">—</TableCell>
            <TableCell align="center">
              <Badge variant="neutral" size="sm">
                12 products
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

      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add New Category"
        description="Create a category to group fabric items."
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
              Save Category
            </Button>
          </>
        }
      >
        <div className="space-y-3.5">
          <Input label="Category Name" placeholder="e.g. Winter Shawls & Wool" />
          <Input label="Category Code (Optional)" placeholder="e.g. CAT-SHAWLS" />
        </div>
      </Dialog>
    </PageContainer>
  );
}
