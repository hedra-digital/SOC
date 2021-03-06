from openerp import models, fields, api
from datetime import datetime
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError
from odoo.osv import expression

class StockQuantity(models.Model):
    _inherit = 'stock.quant'
    
    flag = fields.Boolean(default=True)
    consig_op_type = fields.Selection([('sale','Sale'),('purchase','Purchase')], string='Consignment Operation Type (SOC)')

    @api.constrains('quantity')
    def _onchange_quantity(self):
        if self.flag:
            self.flag = False
            
            print ("##### _onchange_quantity [START] #####")
            
            stock_quant_consignment = self.env.cr.execute("""select sol.id sol_id, pp.id p_id, sol.consignment_stock consig, sq.quantity qty, sq.location_id loc
        FROM sale_order_line sol
        join product_product pp on pp.id = sol.product_id
        join public.sale_order so on so.order_type = 'con_order' and so.id = sol.order_id 
        join public.stock_quant sq on sq.quantity != sol.consignment_stock and sq.product_id = sol.product_id
        join public.res_partner rp on rp.allow_consignment = true and sq.location_id = rp.consignee_location_id;
            """)
        
        for x in self._cr.dictfetchall():
            sol_for_update = ''
            sol_for_update = self.env['sale.order.line'].search([('id','=', x["sol_id"])])
            sol_for_update["consignment_stock"] = x["qty"]
        self.flag = True
        print ("##### _onchange_quantity [END] #####")

    @api.model
    def create(self, vals):
        if self.env.context.get('consig_op_type', False):
            vals.update({'consig_op_type': self.env.context.get('consig_op_type')})
        return super(StockQuantity, self).create(vals)
    
class stock_location(models.Model):
    _inherit = 'stock.location'

    consignee_id = fields.Many2one('res.partner','Consignatário (SOC)', readonly=True)
    consignment_operation_type = fields.Selection([('sale','Sale'),('purchase','Purchase')], string='Consignment Operation Type (SOC)')
    is_consignment = fields.Boolean('Local de Consignação (SOC)', readonly=True)

    @api.one
    @api.constrains('consignee_id')
    def _check_internal_location(self):
        if self.consignee_id and self.usage != 'internal':
            raise Warning(_('O local de consignação deve ser internal'))


class StockMoves(models.Model):
    _inherit = 'stock.move'
    
    def _update_reserved_quantity(self, need, available_quantity, location_id, lot_id=None, package_id=None, owner_id=None, strict=True):    
        print("##### _update_reserved_quantity [START] #####")
        self.ensure_one()
        if self.sale_line_id.order_id.order_type == 'con_sale':
            location_id = self.sale_line_id.order_id.partner_id.consignee_location_id
        if not lot_id:
            lot_id = self.env['stock.production.lot']
        if not package_id:
            package_id = self.env['stock.quant.package']
        if not owner_id:
            owner_id = self.env['res.partner']
        taken_quantity = min(available_quantity, need)
        quants = []
        try:
            quants = self.env['stock.quant']._update_reserved_quantity(
                self.product_id, location_id, taken_quantity, lot_id=lot_id,
                package_id=package_id, owner_id=owner_id, strict=strict
            )
        except UserError:
            # If it raises here, it means that the `available_quantity` brought by a done move line
            # is not available on the quants itself. This could be the result of an inventory
            # adjustment that removed totally of partially `available_quantity`. When this happens, we
            # chose to do nothing. This situation could not happen on MTS move, because in this case
            # `available_quantity` is directly the quantity on the quants themselves.
            taken_quantity = 0

        # Find a candidate move line to update or create a new one.
        for reserved_quant, quantity in quants:
            to_update = self.move_line_ids.filtered(lambda m: m.location_id.id == reserved_quant.location_id.id and m.lot_id.id == reserved_quant.lot_id.id and m.package_id.id == reserved_quant.package_id.id and m.owner_id.id == reserved_quant.owner_id.id)
            if to_update:
                to_update[0].with_context(bypass_reservation_update=True).product_uom_qty += self.product_id.uom_id._compute_quantity(quantity, self.product_uom, rounding_method='HALF-UP')
            else:
                if self.product_id.tracking == 'serial':
                    for i in range(0, int(quantity)):
                        self.env['stock.move.line'].create(self._prepare_move_line_vals(quantity=1, reserved_quant=reserved_quant))
                else:
                    self.env['stock.move.line'].create(self._prepare_move_line_vals(quantity=quantity, reserved_quant=reserved_quant))
        print("##### _update_reserved_quantity [END] #####")
        return taken_quantity
    
    def _prepare_move_line_vals(self, quantity=None, reserved_quant=None):
        rec = super(StockMoves, self)._prepare_move_line_vals(quantity=None, reserved_quant=None)   
        print ("##### _prepare_move_line_vals [START] #####")
        src_loc_id = ''
        dest_loc_id = ''
        if self.sale_line_id.order_id.order_type == 'con_order':
            dest_loc_id = self.sale_line_id.order_id.partner_id.consignee_location_id.id
            
        elif self.sale_line_id.order_id.order_type == 'con_sale':
            src_loc_id = self.sale_line_id.order_id.partner_id.consignee_location_id.id,
            
        self.ensure_one()
        # apply putaway
        location_dest_id = self.location_dest_id.get_putaway_strategy(self.product_id).id or self.location_dest_id.id
        vals = {
            'move_id': self.id,
            'product_id': self.product_id.id,
            'product_uom_id': self.product_uom.id,
            'location_id': src_loc_id or self.location_id.id,
            'location_dest_id': dest_loc_id or location_dest_id,
            'picking_id': self.picking_id.id,
        }
        if quantity:
            uom_quantity = self.product_id.uom_id._compute_quantity(quantity, self.product_uom, rounding_method='HALF-UP')
            
            vals = dict(vals, product_uom_qty=uom_quantity)
            
        if reserved_quant:
            vals = dict(
                vals,
                location_id=reserved_quant.location_id.id,
                lot_id=reserved_quant.lot_id.id or False,
                package_id=reserved_quant.package_id.id or False,
                owner_id =reserved_quant.owner_id.id or False,
            )
        
        print ("##### _prepare_move_line_vals [END] #####")
        
        return vals

class ProcurementGroup(models.Model):
    _inherit = 'procurement.group'

    @api.model
    def _search_rule(self, route_ids, product_id, warehouse_id, domain):
        """ First find a rule among the ones defined on the procurement
        group, then try on the routes defined for the product, finally fallback
        on the default behavior
        """
        if warehouse_id:
            domain = expression.AND([['|', ('warehouse_id', '=', warehouse_id.id), ('warehouse_id', '=', False)], domain])
        Rule = self.env['stock.rule']
        res = self.env['stock.rule']
        stock_quant = self.env['stock.quant']
        if route_ids:
            res = Rule.search(expression.AND([[('route_id', 'in', route_ids.ids)], domain]), order='route_sequence, sequence', limit=1)
        if not res:
            product_routes = product_id.route_ids | product_id.categ_id.total_route_ids
            if product_routes:
                res = Rule.search(expression.AND([[('route_id', 'in', product_routes.ids)], domain]), order='route_sequence, sequence', limit=1)
            stock_quant_id = stock_quant.search([('product_id', '=', product_id.id), ('location_id.is_consignment', '=', True)], limit=1)
            order_type = self.env.context['order_type']
            if stock_quant_id and order_type == 'sale':
                res = Rule.search(expression.AND([[('location_src_id', '=', stock_quant_id.location_id.id)], domain]))
        if not res and warehouse_id:
            warehouse_routes = warehouse_id.route_ids
            if warehouse_routes:
                res = Rule.search(expression.AND([[('route_id', 'in', warehouse_routes.ids)], domain]), order='route_sequence, sequence', limit=1)
        return res

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model
    def create(self, vals):
        if self.env.context.get('order_type', False) and self.env.context.get('order_type') == 'con_order':
            partner_id = self.env['res.partner'].search([('id', '=', vals.get('partner_id'))])
            vals.update({'location_dest_id': partner_id.consignee_location_id.id})
        return super(StockPicking, self).create(vals)
