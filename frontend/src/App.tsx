import { Route, Routes } from 'react-router-dom';
import { CustomCursor } from './components/shared/CustomCursor';
import { GlobeStageProvider, useGlobeStage } from './components/globe/GlobeStage';
import { NavBar } from './components/shared/NavBar';
import { useAegisSocket } from './lib/websocket';
import Landing from './pages/Landing';
import Monitor from './pages/Monitor';
import Negotiate from './pages/Negotiate';
import Trajectory from './pages/Trajectory';
import History from './pages/History';
import About from './pages/About';
import Cloud from './pages/Cloud';

export default function App() {
  // Mounted once here (not per-page) so the connection and its status
  // persist across route changes — the NavBar's live indicator needs it
  // regardless of which page is showing.
  useAegisSocket();

  return (
    <GlobeStageProvider>
      <CustomCursor />
      <AppShell />
    </GlobeStageProvider>
  );
}

function AppShell() {
  const { isGlobeInteractive } = useGlobeStage();
  const routeLayerClassName = [
    'relative z-10',
    isGlobeInteractive ? 'pointer-events-none' : 'pointer-events-auto',
  ].join(' ');

  return (
    <>
      <div className="relative z-20 pointer-events-auto">
        <NavBar />
      </div>
      <div className={routeLayerClassName}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/monitor" element={<Monitor />} />
          <Route path="/negotiate/:id?" element={<Negotiate />} />
          <Route path="/negotiate/:id/trajectory" element={<Trajectory />} />
          <Route path="/history" element={<History />} />
          <Route path="/cloud" element={<Cloud />} />
          <Route path="/about" element={<About />} />
        </Routes>
      </div>
    </>
  );
}
