import React, { useState, useEffect } from 'react';
import { FaPython } from 'react-icons/fa';
import { SiMinio, SiTemporal, SiGrafana, SiReadthedocs } from 'react-icons/si';
import { BiLineChart } from 'react-icons/bi';

export default function App() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const getSubdomainUrl = (subdomain) => {
    const protocol = window.location.protocol;
    const hostname = window.location.hostname;
    const url = `${protocol}//${subdomain}.${hostname}`;

    console.debug('[Scout] getSubdomainUrl', { subdomain, hostname, protocol, url });

    return url;
  };

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-blue-50 via-white to-purple-50 p-6 md:p-8">
      <div
        className={`max-w-6xl mx-auto transition-all duration-1000 ${mounted ? 'opacity-100' : 'opacity-0'}`}
      >
        {/* Hero Section */}
        <div className="flex flex-col items-center justify-center py-16 md:py-24 text-center">
          <h1 className="text-6xl md:text-8xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-purple-600 mb-6 tracking-tight">
            Welcome to Scout
          </h1>
          <p className="text-lg md:text-xl text-gray-600 max-w-xl">
            A radiology report exploration tool brought to you by the Translational AI Group (TAG)
            at WashU
          </p>
        </div>

        {/* Main Tools Section */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8 mb-12">
          <a
            href={getSubdomainUrl('jupyter')}
            className="group flex items-center justify-center p-8 md:p-10 bg-white bg-opacity-70 backdrop-blur-sm rounded-2xl shadow-lg hover:shadow-xl transition-all duration-300 hover:-translate-y-1 border border-blue-100 no-underline"
          >
            <div className="flex flex-col md:flex-row items-center text-center md:text-left gap-6">
              <div className="p-5 rounded-full bg-blue-50 text-blue-600 group-hover:bg-blue-100 transition-colors duration-300">
                <FaPython className="text-6xl md:text-7xl" />
              </div>
              <div>
                <h2 className="text-3xl md:text-4xl font-semibold text-gray-800 mb-2">Notebooks</h2>
                <p className="text-gray-600">
                  Launch interactive notebooks for data analysis with JupyterHub
                </p>
              </div>
            </div>
          </a>

          <a
            href={getSubdomainUrl('superset')}
            className="group flex items-center justify-center p-8 md:p-10 bg-white bg-opacity-70 backdrop-blur-sm rounded-2xl shadow-lg hover:shadow-xl transition-all duration-300 hover:-translate-y-1 border border-yellow-100 no-underline"
          >
            <div className="flex flex-col md:flex-row items-center text-center md:text-left gap-6">
              <div className="p-5 rounded-full bg-yellow-50 text-yellow-600 group-hover:bg-yellow-100 transition-colors duration-300">
                <BiLineChart className="text-6xl md:text-7xl" />
              </div>
              <div>
                <h2 className="text-3xl md:text-4xl font-semibold text-gray-800 mb-2">Analytics</h2>
                <p className="text-gray-600">
                  Explore data visualizations and dashboards with Superset
                </p>
              </div>
            </div>
          </a>
        </div>

        {/* Documentation Section */}
        <div className="mb-12">
          <a
            href="https://washu-scout.readthedocs.io/en/latest/"
            target="_blank"
            rel="noopener noreferrer"
            className="group w-full flex items-center justify-center p-6 md:p-8 bg-white bg-opacity-70 backdrop-blur-sm rounded-xl shadow-md hover:shadow-lg transition-all duration-300 border border-purple-100 no-underline"
          >
            <div className="flex items-center gap-4">
              <div className="p-4 rounded-full bg-purple-50 text-purple-600 group-hover:bg-purple-100 transition-colors duration-300">
                <SiReadthedocs className="text-3xl" />
              </div>
              <div className="text-left">
                <h2 className="text-2xl font-medium text-gray-800">Documentation</h2>
                <p className="text-gray-600">Learn how to use Scout effectively</p>
              </div>
            </div>
          </a>
        </div>

        {/* Admin Tools Section */}
        <div className="bg-white bg-opacity-80 backdrop-blur-sm rounded-xl shadow-lg p-6 mb-12">
          <h2 className="text-xl font-semibold text-gray-700 mb-6 flex items-center">
            <span className="inline-block w-1 h-6 bg-gray-700 mr-3 rounded"></span>
            Admin Tools
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <a
              href={getSubdomainUrl('minio')}
              className="flex items-center gap-3 p-4 rounded-lg bg-gradient-to-br from-red-50 to-white text-red-700 border border-red-100 shadow hover:shadow-md transition-all duration-300 no-underline"
            >
              <SiMinio className="text-2xl" />
              <span className="font-medium">Lake</span>
            </a>

            <a
              href={getSubdomainUrl('temporal')}
              className="flex items-center gap-3 p-4 rounded-lg bg-gradient-to-br from-emerald-50 to-white text-emerald-700 border border-emerald-100 shadow hover:shadow-md transition-all duration-300 no-underline"
            >
              <SiTemporal className="text-2xl" />
              <span className="font-medium">Orchestrator</span>
            </a>

            <a
              href={getSubdomainUrl('grafana')}
              className="flex items-center gap-3 p-4 rounded-lg bg-gradient-to-br from-orange-50 to-white text-orange-700 border border-orange-100 shadow hover:shadow-md transition-all duration-300 no-underline"
            >
              <SiGrafana className="text-2xl" />
              <span className="font-medium">Monitor</span>
            </a>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-gray-500 text-sm">
          © {new Date().getFullYear()} Translational AI Group, Washington University in St. Louis
        </div>
      </div>
    </div>
  );
}
