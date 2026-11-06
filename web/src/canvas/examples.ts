/** Example questions on the empty canvas: one click asks them. */

export type ExampleGroup = { title: string; description: string; questions: readonly string[] }

export const EXAMPLE_GROUPS: readonly ExampleGroup[] = [
  {
    title: 'Northwind shop',
    description: 'Orders, products, customers and staff.',
    // The five the first UI (Streamlit, retired in U6.6) offered.
    questions: [
      'How many customers are there?',
      'What are the 5 most expensive products?',
      'Which employee handled the most orders?',
      'How many orders did each country place?',
      'What is the total sales amount per category?',
    ],
  },
  {
    title: 'Chinook music store',
    description: 'Tracks, albums, artists and invoices.',
    // From evals/golden/chinook.yaml.
    questions: [
      'How many tracks does the music store have?',
      'How many tracks are there of each media type?',
      "What were the music store's sales in each year?",
    ],
  },
  {
    title: 'Business terms',
    description: 'Words defined in the semantic layer, like "key account".',
    // From evals/golden/northwind_business.yaml.
    questions: [
      'How many key accounts do we have?',
      'Which products need restocking?',
      'What was our average order value in 1997?',
    ],
  },
]
