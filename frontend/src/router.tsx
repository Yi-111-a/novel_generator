import { createBrowserRouter, Navigate } from 'react-router-dom';
import { ErrorPage, ProjectLayout, RootLayout } from './components/Layouts';
import { Dashboard } from './views/Dashboard';
import { LedgerInspector } from './views/LedgerInspector';
import { Outline } from './views/Outline';
import { Reading } from './views/Reading';
import { Settings } from './views/Settings';
import { Simulation } from './views/Simulation';
import { SeedWorkshop } from './views/SeedWorkshop';
import { WorldConfig } from './views/WorldConfig';
import { Inspect } from './views/Inspect';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    errorElement: <ErrorPage />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'settings', element: <Settings /> },
      {
        path: 'p/:projectId',
        element: <ProjectLayout />,
        errorElement: <ErrorPage />,
        children: [
          { index: true, element: <Navigate to="seed" replace /> },
          { path: 'seed', element: <SeedWorkshop /> },
          { path: 'world', element: <WorldConfig /> },
          { path: 'outline', element: <Outline /> },
          { path: 'sim', element: <Simulation /> },
          { path: 'read', element: <Reading /> },
          { path: 'ledger', element: <LedgerInspector /> },
          { path: 'inspect', element: <Inspect /> },
        ],
      },
    ],
  },
]);
