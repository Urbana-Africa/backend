# Seed Sales Functionality

## Overview

The seed sales endpoint (`/pay/seed-sales`) is a testing utility that generates realistic test data for the Urbana marketplace. It creates orders, payments, escrows, and related records to simulate real marketplace activity.

## What It Does

For every designer in the system, the seed sales endpoint creates:

- **5 successful sales**: Orders marked as "delivered" with released escrows
- **2 returned sales**: Orders marked as "returned" with refunded escrows

## Generated Data Structure

### Customer
- Username: `seed_customer`
- Email: `seed@example.com`
- Address: 123 Seed St, Lagos, Nigeria

### Per Designer
- **Orders**: 7 total (5 successful, 2 returned)
- **Order Items**: 7 total (1 per order)
- **Payments**: 7 total (all marked as successful)
- **Escrows**: 7 total (5 released, 2 refunded)
- **Invoices**: 7 total

### Unique Identifiers
- **Order IDs**: Format `URBON-{random}-{timestamp}-{random}`
- **Tracking Numbers**: Format `URBITR-{order_pk}-{timestamp}-{random}`

## Usage

### Running the Endpoint

```bash
# Start the Django server
python manage.py runserver

# Make a GET request to the endpoint
curl http://localhost:8000/pay/seed-sales
```

### Expected Response

```json
{
  "message": "Seeding completed",
  "report": [
    {
      "designer": "Brand Name",
      "successful": 5,
      "returned": 2
    }
  ]
}
```

## Testing

### Running Tests

```bash
# Run all seed sales tests
python run_seed_sales_tests.py test

# Test specific functionality
python run_seed_sales_tests.py specific SeedSalesViewTest
python run_seed_sales_tests.py specific SeedSalesViewTest.test_seed_sales_creates_unique_order_ids

# Test the actual endpoint
python run_seed_sales_tests.py endpoint
```

### Verification

```bash
# Verify created data
python verify_seed_sales.py

# Clean up test data
python verify_seed_sales.py cleanup
```

## Test Coverage

The test suite covers:

- ✅ **Uniqueness Constraints**: Ensures no duplicate order IDs or tracking numbers
- ✅ **Data Integrity**: Verifies all related records are created correctly
- ✅ **Status Handling**: Confirms successful vs returned orders have proper statuses
- ✅ **Escrow Processing**: Validates escrow services are called appropriately
- ✅ **Multi-Designer Support**: Tests with multiple designers
- ✅ **Error Handling**: Ensures endpoint only works in DEBUG mode
- ✅ **Missing Product Creation**: Creates designer products if they don't exist

## Key Fixes Applied

### 1. Random Module Usage
**Problem**: `'module' object is not callable. Did you mean: 'random.random(...)'?`
**Solution**: Changed `random()` to `random.random()` throughout the codebase

### 2. Unique Constraint Violations
**Problem**: `UNIQUE constraint failed: customers_order.order_id`
**Solution**: Added explicit `order_id` generation with unique pattern

**Problem**: `UNIQUE constraint failed: customers_orderitem.tracking_number`
**Solution**: Added explicit `tracking_number` generation with unique pattern

## Security

- The endpoint only works when `DEBUG = True`
- No authentication required (for testing convenience)
- Creates a dedicated test user that doesn't interfere with real users

## Files Modified

### Core Fixes
- `backend/apps/pay/views.py` - Fixed seed sales view
- `backend/apps/customers/views.py` - Fixed random usage in order creation

### Test Files
- `backend/apps/pay/tests/test_seed_sales.py` - Comprehensive test suite
- `backend/run_seed_sales_tests.py` - Test runner script
- `backend/verify_seed_sales.py` - Data verification script

## Best Practices

### Before Running Tests
1. Ensure you're in a development environment (`DEBUG = True`)
2. Back up your database if it contains important data
3. Run tests in a clean environment when possible

### After Testing
1. Use the verification script to confirm data integrity
2. Clean up test data using the cleanup script
3. Verify no production data was affected

## Troubleshooting

### Common Issues

1. **404 Error**: Ensure you're accessing `/pay/seed-sales` not `/api/pay/seed-sales`
2. **403 Forbidden**: Check that `DEBUG = True` in your settings
3. **Unique Constraint Errors**: Run the cleanup script before reseeding
4. **Import Errors**: Make sure your virtual environment is activated

### Debug Mode Check

```python
# In Django shell
from django.conf import settings
print(f"DEBUG mode: {settings.DEBUG}")
```

## Future Enhancements

Potential improvements for the seed sales functionality:

1. **Configurable Numbers**: Allow specifying how many orders to create per designer
2. **Custom Date Ranges**: Generate orders within specific time periods
3. **Product Variety**: Create orders for multiple products per designer
4. **Realistic Customer Data**: Generate more diverse customer profiles
5. **Payment Method Variations**: Include different payment processors
6. **Status Progression**: Simulate order status changes over time

## Contributing

When modifying the seed sales functionality:

1. Update the corresponding tests
2. Verify uniqueness constraints are maintained
3. Test with multiple designers
4. Ensure cleanup scripts still work
5. Update this documentation

---

**Note**: This functionality is intended for development and testing only. Never use in production environments.
