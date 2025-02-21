import React from "react";
import { FaPython } from "react-icons/fa";
import { SiMinio, SiTemporal, SiGrafana } from "react-icons/si";

export default function App() {
  const handleLaunchJupyterHub = () => {
    window.location.href = "/jupyter";
  };
  const handleLaunchMinIO = () => {
    window.location.href = "/minio/";
  };
  const handleLaunchTemporal = () => {
    window.location.href = "/temporal";
  };
  const handleLaunchGrafana = () => {
    window.location.href = "/grafana";
  };

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-gradient-to-br from-gray-50 to-gray-200 p-8">
      <div className="flex w-full max-w-6xl flex-col items-center justify-center space-y-12">
        <div className="text-center animate-fade-in">
          <h1 className="mb-6 text-5xl font-bold text-gray-800">
            Welcome to the WashU Rad Report Explorer!
          </h1>
          <p className="text-2xl text-gray-600">
            Brought to you by the Translational AI Group (TAG)
          </p>
        </div>

        {/* Main JupyterHub Button */}
        <button
          onClick={handleLaunchJupyterHub}
          className="flex items-center justify-center gap-6 rounded-2xl bg-blue-50 p-10 text-blue-700 shadow-lg transition-all hover:bg-blue-100 hover:shadow-xl hover:-translate-y-1"
        >
          <FaPython className="text-7xl" />
          <span className="text-3xl font-semibold">Launch JupyterHub</span>
        </button>

        {/* Admin Tools Section */}
        <div className="w-full max-w-4xl rounded-xl bg-white p-6 shadow-lg">
          <h2 className="mb-6 text-xl font-semibold text-gray-700">Admin Tools</h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <button
              onClick={handleLaunchMinIO}
              className="flex items-center justify-center gap-3 rounded-xl bg-red-50 p-4 text-red-700 shadow transition-all hover:bg-red-100 hover:shadow-md hover:-translate-y-1"
            >
              <SiMinio className="text-3xl" />
              <span className="text-lg font-medium">MinIO</span>
            </button>

            <button
              onClick={handleLaunchTemporal}
              className="flex items-center justify-center gap-3 rounded-xl bg-emerald-50 p-4 text-emerald-700 shadow transition-all hover:bg-emerald-100 hover:shadow-md hover:-translate-y-1"
            >
              <SiTemporal className="text-3xl" />
              <span className="text-lg font-medium">Temporal</span>
            </button>

            <button
              onClick={handleLaunchGrafana}
              className="flex items-center justify-center gap-3 rounded-xl bg-orange-50 p-4 text-orange-700 shadow transition-all hover:bg-orange-100 hover:shadow-md hover:-translate-y-1"
            >
              <SiGrafana className="text-3xl" />
              <span className="text-lg font-medium">Grafana</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Add the fade-in animation
const style = document.createElement('style');
style.textContent = `
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .animate-fade-in {
    animation: fadeIn 0.8s ease-out forwards;
  }
`;
document.head.appendChild(style);
