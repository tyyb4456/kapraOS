import { ArrowLeft, Save } from 'lucide-react';
import { Link } from 'react-router-dom';
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
  CardHeader,
  CardTitle,
} from '../../components/ui/index.ts';

export function NewPurchasePage() {
  return (
    <PageContainer>
      <div className="flex items-center gap-2 mb-2">
        <Link
          to="/purchases"
          className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Purchases
        </Link>
      </div>

      <PageHeader
        title="Record Inward Fabric Purchase"
        description="Receive rolls and consignment stock from mills and textile suppliers."
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">
            Supplier & Consignment Details
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Select label="Supplier Mill">
              <option value="s1">Kohinoor Textile Mills Ltd.</option>
              <option value="s2">Nishat Weaving Mills</option>
              <option value="s3">Al-Karam Textile Mills</option>
            </Select>
            <Input label="Purchase Order / Bilty #" placeholder="e.g. BL-89421" />
            <Input label="Arrival Date" type="date" defaultValue={new Date().toISOString().split('T')[0]} />
          </div>

          <div className="border-t border-zinc-100 pt-4 space-y-3">
            <h4 className="text-xs font-semibold uppercase text-zinc-500 tracking-wider">
              Item Details
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
              <div className="sm:col-span-2">
                <Select label="Fabric Product">
                  <option>Egyptian Lawn - Plain Dyed</option>
                  <option>Italian Wool Blend 120s</option>
                </Select>
              </div>
              <Input label="Quantity (Meters / Rolls)" type="number" placeholder="0.00" />
              <Input label="Unit Cost Price (PKR)" type="number" placeholder="0.00" />
            </div>
          </div>

          <div className="border-t border-zinc-100 pt-4 flex justify-end gap-2">
            <Link to="/purchases">
              <Button variant="outline" size="sm">
                Cancel
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Save className="w-4 h-4" />}
            >
              Record Consignment
            </Button>
          </div>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
