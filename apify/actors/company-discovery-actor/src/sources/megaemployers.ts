import type { CompanyDiscovery } from '../types.js';

/**
 * Curated list of mega employers from India, China, US, Europe, and other regions.
 * These companies use enterprise ATS (Workday, Taleo, SuccessFactors) rather than
 * Greenhouse/Lever, so they won't appear in the ATS API sources.
 *
 * estimated_jobs is based on publicly reported vacancy rates (5-10% of headcount)
 * for companies that are actively hiring at scale.
 */

interface MegaEmployer {
  name: string;
  url: string;
  jobs: number;
  region: string;
}

const MEGA_EMPLOYERS: MegaEmployer[] = [
  // ── India — IT Services & Tech ──
  { name: 'Tata Consultancy Services', url: 'https://ibegin.tcs.com/iBegin/jobs', jobs: 45000, region: 'india' },
  { name: 'Infosys', url: 'https://career.infosys.com', jobs: 28000, region: 'india' },
  { name: 'Wipro', url: 'https://careers.wipro.com', jobs: 20000, region: 'india' },
  { name: 'HCL Technologies', url: 'https://www.hcltech.com/careers', jobs: 18000, region: 'india' },
  { name: 'Tech Mahindra', url: 'https://careers.techmahindra.com', jobs: 12000, region: 'india' },
  { name: 'Cognizant', url: 'https://careers.cognizant.com', jobs: 15000, region: 'india' },
  { name: 'LTIMindtree', url: 'https://www.ltimindtree.com/careers', jobs: 8000, region: 'india' },
  { name: 'Mphasis', url: 'https://careers.mphasis.com', jobs: 4000, region: 'india' },
  { name: 'Persistent Systems', url: 'https://www.persistent.com/careers', jobs: 3000, region: 'india' },
  { name: 'Hexaware Technologies', url: 'https://hexaware.com/careers', jobs: 3000, region: 'india' },
  // India — Conglomerates, Banking, Consumer
  { name: 'Tata Group', url: 'https://www.tata.com/careers', jobs: 50000, region: 'india' },
  { name: 'Reliance Industries', url: 'https://careers.ril.com', jobs: 30000, region: 'india' },
  { name: 'Adani Group', url: 'https://www.adani.com/careers', jobs: 15000, region: 'india' },
  { name: 'Mahindra Group', url: 'https://careers.mahindra.com', jobs: 15000, region: 'india' },
  { name: 'Larsen & Toubro', url: 'https://www.larsentoubro.com/careers', jobs: 25000, region: 'india' },
  { name: 'HDFC Bank', url: 'https://www.hdfcbank.com/personal/useful-links/careers', jobs: 12000, region: 'india' },
  { name: 'ICICI Bank', url: 'https://www.icicicareers.com', jobs: 10000, region: 'india' },
  { name: 'State Bank of India', url: 'https://sbi.co.in/web/careers', jobs: 18000, region: 'india' },
  { name: 'Axis Bank', url: 'https://www.axisbank.com/careers', jobs: 6000, region: 'india' },
  { name: 'Kotak Mahindra Bank', url: 'https://www.kotak.com/en/careers', jobs: 5000, region: 'india' },
  { name: 'Bajaj Finserv', url: 'https://www.bajajfinserv.in/careers', jobs: 8000, region: 'india' },
  { name: 'Flipkart', url: 'https://www.flipkartcareers.com', jobs: 5000, region: 'india' },
  { name: 'Swiggy', url: 'https://careers.swiggy.com', jobs: 3000, region: 'india' },
  { name: 'Zomato', url: 'https://www.zomato.com/careers', jobs: 2500, region: 'india' },
  { name: 'Paytm', url: 'https://paytm.com/careers', jobs: 2000, region: 'india' },
  { name: 'Ola Cabs', url: 'https://www.olacabs.com/careers', jobs: 2000, region: 'india' },
  { name: 'Razorpay', url: 'https://razorpay.com/jobs', jobs: 1500, region: 'india' },
  { name: 'BYJU\'S', url: 'https://byjus.com/careers', jobs: 2000, region: 'india' },
  { name: 'PhonePe', url: 'https://www.phonepe.com/careers', jobs: 2000, region: 'india' },
  { name: 'Dream11', url: 'https://www.dream11.com/careers', jobs: 1000, region: 'india' },
  { name: 'Tata Motors', url: 'https://careers.tatamotors.com', jobs: 10000, region: 'india' },
  { name: 'Maruti Suzuki', url: 'https://www.marutisuzuki.com/corporate/careers', jobs: 5000, region: 'india' },
  { name: 'Sun Pharmaceutical', url: 'https://www.sunpharma.com/careers', jobs: 5000, region: 'india' },
  { name: 'Dr. Reddy\'s Laboratories', url: 'https://careers.drreddys.com', jobs: 3000, region: 'india' },
  { name: 'Hindustan Unilever', url: 'https://careers.unilever.com/in/en', jobs: 4000, region: 'india' },
  { name: 'ITC Limited', url: 'https://www.itcportal.com/careers', jobs: 5000, region: 'india' },
  { name: 'Asian Paints', url: 'https://www.asianpaints.com/more/careers', jobs: 2000, region: 'india' },

  // ── China — Tech Giants ──
  { name: 'Alibaba Group', url: 'https://talent.alibaba.com', jobs: 18000, region: 'china' },
  { name: 'Tencent', url: 'https://careers.tencent.com', jobs: 8000, region: 'china' },
  { name: 'Huawei', url: 'https://career.huawei.com', jobs: 15000, region: 'china' },
  { name: 'ByteDance', url: 'https://jobs.bytedance.com', jobs: 10000, region: 'china' },
  { name: 'JD.com', url: 'https://campus.jd.com', jobs: 28000, region: 'china' },
  { name: 'Baidu', url: 'https://talent.baidu.com', jobs: 3000, region: 'china' },
  { name: 'Xiaomi', url: 'https://hr.xiaomi.com', jobs: 3000, region: 'china' },
  { name: 'Meituan', url: 'https://zhaopin.meituan.com', jobs: 8000, region: 'china' },
  { name: 'Pinduoduo', url: 'https://careers.pinduoduo.com', jobs: 5000, region: 'china' },
  { name: 'NetEase', url: 'https://hr.163.com', jobs: 3000, region: 'china' },
  { name: 'DiDi Global', url: 'https://talent.didiglobal.com', jobs: 3000, region: 'china' },
  // China — Manufacturing & Industrial
  { name: 'BYD Company', url: 'https://job.byd.com', jobs: 45000, region: 'china' },
  { name: 'Foxconn', url: 'https://www.foxconn.com/en/careers', jobs: 55000, region: 'china' },
  { name: 'Lenovo', url: 'https://www.lenovo.com/careers', jobs: 5000, region: 'china' },
  { name: 'CATL', url: 'https://www.catl.com/en/career', jobs: 12000, region: 'china' },
  { name: 'Midea Group', url: 'https://careers.midea.com', jobs: 10000, region: 'china' },
  { name: 'Haier Group', url: 'https://www.haier.com/global/careers', jobs: 8000, region: 'china' },
  { name: 'ZTE Corporation', url: 'https://www.zte.com.cn/careers', jobs: 4000, region: 'china' },
  { name: 'SMIC', url: 'https://www.smics.com/en/careers', jobs: 3000, region: 'china' },
  // China — Banking & Finance
  { name: 'ICBC', url: 'https://job.icbc.com.cn', jobs: 20000, region: 'china' },
  { name: 'China Construction Bank', url: 'https://job.ccb.com', jobs: 15000, region: 'china' },
  { name: 'Agricultural Bank of China', url: 'https://job.abchina.com', jobs: 15000, region: 'china' },
  { name: 'Bank of China', url: 'https://job.bank-of-china.com', jobs: 12000, region: 'china' },
  { name: 'Ping An Insurance', url: 'https://talent.pingan.com', jobs: 20000, region: 'china' },
  { name: 'China Life Insurance', url: 'https://www.chinalife.com.cn/careers', jobs: 10000, region: 'china' },
  // China — Telecom & State Enterprise
  { name: 'China Mobile', url: 'https://www.chinamobileltd.com/careers', jobs: 8000, region: 'china' },
  { name: 'China Telecom', url: 'https://www.chinatelecom-h.com/careers', jobs: 6000, region: 'china' },
  { name: 'Sinopec', url: 'https://www.sinopecgroup.com/careers', jobs: 15000, region: 'china' },
  { name: 'PetroChina', url: 'https://www.petrochina.com.cn/careers', jobs: 12000, region: 'china' },
  { name: 'China State Construction', url: 'https://www.cscec.com/careers', jobs: 20000, region: 'china' },
  { name: 'China Railway Group', url: 'https://www.crec.cn/careers', jobs: 15000, region: 'china' },

  // ── United States — Mega Employers ──
  { name: 'Amazon', url: 'https://amazon.jobs', jobs: 100000, region: 'us' },
  { name: 'Walmart', url: 'https://careers.walmart.com', jobs: 150000, region: 'us' },
  { name: 'UPS', url: 'https://www.jobs-ups.com', jobs: 40000, region: 'us' },
  { name: 'FedEx', url: 'https://careers.fedex.com', jobs: 35000, region: 'us' },
  { name: 'Apple', url: 'https://jobs.apple.com', jobs: 12000, region: 'us' },
  { name: 'Google', url: 'https://careers.google.com', jobs: 10000, region: 'us' },
  { name: 'Microsoft', url: 'https://careers.microsoft.com', jobs: 15000, region: 'us' },
  { name: 'Meta', url: 'https://www.metacareers.com', jobs: 8000, region: 'us' },
  { name: 'JPMorgan Chase', url: 'https://careers.jpmorgan.com', jobs: 25000, region: 'us' },
  { name: 'Bank of America', url: 'https://careers.bankofamerica.com', jobs: 20000, region: 'us' },
  { name: 'Citigroup', url: 'https://jobs.citi.com', jobs: 15000, region: 'us' },
  { name: 'Wells Fargo', url: 'https://www.wellsfargojobs.com', jobs: 15000, region: 'us' },
  { name: 'Deloitte', url: 'https://www2.deloitte.com/careers', jobs: 30000, region: 'us' },
  { name: 'PricewaterhouseCoopers', url: 'https://www.pwc.com/gx/en/careers', jobs: 25000, region: 'us' },
  { name: 'Ernst & Young', url: 'https://www.ey.com/en_us/careers', jobs: 20000, region: 'us' },
  { name: 'KPMG', url: 'https://home.kpmg/careers', jobs: 15000, region: 'us' },
  { name: 'Accenture', url: 'https://www.accenture.com/careers', jobs: 40000, region: 'us' },
  { name: 'UnitedHealth Group', url: 'https://careers.unitedhealthgroup.com', jobs: 30000, region: 'us' },
  { name: 'CVS Health', url: 'https://jobs.cvshealth.com', jobs: 25000, region: 'us' },
  { name: 'HCA Healthcare', url: 'https://careers.hcahealthcare.com', jobs: 25000, region: 'us' },
  { name: 'Kaiser Permanente', url: 'https://jobs.kaiserpermanente.org', jobs: 20000, region: 'us' },
  { name: 'Kroger', url: 'https://jobs.kroger.com', jobs: 25000, region: 'us' },
  { name: 'Target', url: 'https://corporate.target.com/careers', jobs: 20000, region: 'us' },
  { name: 'Home Depot', url: 'https://careers.homedepot.com', jobs: 15000, region: 'us' },
  { name: 'Starbucks', url: 'https://www.starbucks.com/careers', jobs: 15000, region: 'us' },
  { name: 'McDonald\'s', url: 'https://careers.mcdonalds.com', jobs: 20000, region: 'us' },
  { name: 'Boeing', url: 'https://jobs.boeing.com', jobs: 10000, region: 'us' },
  { name: 'Lockheed Martin', url: 'https://www.lockheedmartinjobs.com', jobs: 12000, region: 'us' },
  { name: 'Raytheon Technologies', url: 'https://careers.rtx.com', jobs: 10000, region: 'us' },
  { name: 'General Motors', url: 'https://search-careers.gm.com', jobs: 5000, region: 'us' },
  { name: 'Ford Motor', url: 'https://corporate.ford.com/careers', jobs: 5000, region: 'us' },
  { name: 'Oracle', url: 'https://www.oracle.com/careers', jobs: 10000, region: 'us' },
  { name: 'IBM', url: 'https://www.ibm.com/careers', jobs: 8000, region: 'us' },
  { name: 'Intel', url: 'https://jobs.intel.com', jobs: 5000, region: 'us' },
  { name: 'Nvidia', url: 'https://www.nvidia.com/careers', jobs: 4000, region: 'us' },
  { name: 'Tesla', url: 'https://www.tesla.com/careers', jobs: 8000, region: 'us' },
  { name: 'Johnson & Johnson', url: 'https://www.careers.jnj.com', jobs: 10000, region: 'us' },
  { name: 'Pfizer', url: 'https://www.pfizer.com/careers', jobs: 5000, region: 'us' },
  { name: 'Anthem / Elevance Health', url: 'https://careers.elevancehealth.com', jobs: 10000, region: 'us' },
  { name: 'Goldman Sachs', url: 'https://www.goldmansachs.com/careers', jobs: 5000, region: 'us' },

  // ── Europe ──
  { name: 'Siemens', url: 'https://jobs.siemens.com', jobs: 15000, region: 'europe' },
  { name: 'SAP', url: 'https://jobs.sap.com', jobs: 8000, region: 'europe' },
  { name: 'Volkswagen Group', url: 'https://www.volkswagen-karriere.de', jobs: 12000, region: 'europe' },
  { name: 'BMW Group', url: 'https://www.bmwgroup.jobs', jobs: 8000, region: 'europe' },
  { name: 'Mercedes-Benz', url: 'https://jobs.mercedes-benz.com', jobs: 8000, region: 'europe' },
  { name: 'Bosch', url: 'https://www.bosch.com/careers', jobs: 12000, region: 'europe' },
  { name: 'Deutsche Bank', url: 'https://careers.db.com', jobs: 5000, region: 'europe' },
  { name: 'Allianz', url: 'https://careers.allianz.com', jobs: 5000, region: 'europe' },
  { name: 'BASF', url: 'https://www.basf.com/careers', jobs: 5000, region: 'europe' },
  { name: 'Nestle', url: 'https://www.nestle.com/jobs', jobs: 10000, region: 'europe' },
  { name: 'Novartis', url: 'https://www.novartis.com/careers', jobs: 5000, region: 'europe' },
  { name: 'Roche', url: 'https://careers.roche.com', jobs: 5000, region: 'europe' },
  { name: 'Unilever', url: 'https://careers.unilever.com', jobs: 8000, region: 'europe' },
  { name: 'Shell', url: 'https://careers.shell.com', jobs: 5000, region: 'europe' },
  { name: 'TotalEnergies', url: 'https://careers.totalenergies.com', jobs: 5000, region: 'europe' },
  { name: 'HSBC', url: 'https://www.hsbc.com/careers', jobs: 10000, region: 'europe' },
  { name: 'Barclays', url: 'https://home.barclays/careers', jobs: 5000, region: 'europe' },
  { name: 'NHS', url: 'https://www.jobs.nhs.uk', jobs: 100000, region: 'europe' },
  { name: 'Tesco', url: 'https://www.tesco-careers.com', jobs: 15000, region: 'europe' },
  { name: 'Philips', url: 'https://www.careers.philips.com', jobs: 4000, region: 'europe' },
  { name: 'Ericsson', url: 'https://www.ericsson.com/en/careers', jobs: 4000, region: 'europe' },
  { name: 'Nokia', url: 'https://www.nokia.com/careers', jobs: 3000, region: 'europe' },
  { name: 'Airbus', url: 'https://www.airbus.com/careers', jobs: 8000, region: 'europe' },
  { name: 'Stellantis', url: 'https://www.stellantis.com/careers', jobs: 8000, region: 'europe' },
  { name: 'AstraZeneca', url: 'https://careers.astrazeneca.com', jobs: 5000, region: 'europe' },
  { name: 'GSK', url: 'https://www.gsk.com/en-gb/careers', jobs: 5000, region: 'europe' },
  { name: 'Sanofi', url: 'https://www.sanofi.com/en/careers', jobs: 4000, region: 'europe' },
  { name: 'Deutsche Post DHL', url: 'https://careers.dhl.com', jobs: 20000, region: 'europe' },

  // ── Japan, Korea, Australia ──
  { name: 'Toyota', url: 'https://global.toyota/en/careers', jobs: 10000, region: 'apac' },
  { name: 'Sony', url: 'https://www.sony.com/en/careers', jobs: 5000, region: 'apac' },
  { name: 'Honda', url: 'https://global.honda/careers', jobs: 5000, region: 'apac' },
  { name: 'Samsung Electronics', url: 'https://www.samsung.com/global/careers', jobs: 15000, region: 'apac' },
  { name: 'Hyundai Motor', url: 'https://talent.hyundai.com', jobs: 5000, region: 'apac' },
  { name: 'SK Hynix', url: 'https://recruit.skhynix.com', jobs: 5000, region: 'apac' },
  { name: 'LG Electronics', url: 'https://www.lg.com/global/careers', jobs: 5000, region: 'apac' },
  { name: 'SoftBank', url: 'https://recruit.softbank.jp', jobs: 3000, region: 'apac' },
  { name: 'NTT Group', url: 'https://group.ntt/en/careers', jobs: 8000, region: 'apac' },
  { name: 'Mitsubishi Group', url: 'https://www.mitsubishicorp.com/careers', jobs: 8000, region: 'apac' },
  { name: 'Panasonic', url: 'https://careers.panasonic.com', jobs: 5000, region: 'apac' },
  { name: 'BHP Group', url: 'https://www.bhp.com/careers', jobs: 4000, region: 'apac' },
  { name: 'Commonwealth Bank', url: 'https://www.commbank.com.au/about-us/careers', jobs: 3000, region: 'apac' },
  { name: 'Telstra', url: 'https://careers.telstra.com', jobs: 2000, region: 'apac' },

  // ── Middle East, Africa, Latin America ──
  { name: 'Saudi Aramco', url: 'https://www.aramco.com/en/careers', jobs: 5000, region: 'mea' },
  { name: 'Emirates Group', url: 'https://www.emirates.com/careers', jobs: 5000, region: 'mea' },
  { name: 'Etisalat', url: 'https://www.etisalat.ae/en/careers', jobs: 2000, region: 'mea' },
  { name: 'MTN Group', url: 'https://www.mtn.com/careers', jobs: 3000, region: 'mea' },
  { name: 'Safaricom', url: 'https://www.safaricom.co.ke/careers', jobs: 1500, region: 'mea' },
  { name: 'Mercado Libre', url: 'https://careers.mercadolibre.com', jobs: 5000, region: 'latam' },
  { name: 'Petrobras', url: 'https://www.petrobras.com.br/en/careers', jobs: 5000, region: 'latam' },
  { name: 'Itau Unibanco', url: 'https://carreiras.itau.com.br', jobs: 5000, region: 'latam' },
  { name: 'Banco Bradesco', url: 'https://www.bradesco.com.br/carreiras', jobs: 4000, region: 'latam' },
  { name: 'Globant', url: 'https://www.globant.com/careers', jobs: 3000, region: 'latam' },
];

export function discoverFromMegaEmployers(): CompanyDiscovery[] {
  const now = new Date().toISOString();

  const results = MEGA_EMPLOYERS.map(e => ({
    company_name: e.name,
    job_board_url: e.url,
    estimated_jobs: e.jobs,
    source: `mega-employers-${e.region}`,
    discovered_at: now,
  }));

  const total = results.reduce((s, c) => s + c.estimated_jobs, 0);
  const byRegion = new Map<string, { count: number; jobs: number }>();
  for (const e of MEGA_EMPLOYERS) {
    const r = byRegion.get(e.region) || { count: 0, jobs: 0 };
    r.count++;
    r.jobs += e.jobs;
    byRegion.set(e.region, r);
  }

  console.log(`MegaEmployers: ${results.length} companies, ${total.toLocaleString()} total estimated jobs`);
  for (const [region, stats] of byRegion) {
    console.log(`  ${region}: ${stats.count} companies, ${stats.jobs.toLocaleString()} jobs`);
  }

  return results;
}
