import { fetchJson, processInBatches } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

// 790 confirmed Greenhouse board tokens extracted from boards.csv
const TOKENS = `
10xgenomics 2k 6sense abacusinsights abinbev abnormalsecurity accelschools accenturefederalservices acommerce adamevenyc addepar1 adyen aevexaerospace affinipay1 affirm
agecareers agencywithin agoda aift airtable airtrunk akidolabs alarmcom alixpartners allcareers allencontrolsystems alliancedefendingfreedom allinc aloyoga alpaca
alphafmcroles alphasense alphasenseindia altentechnologyusa altium alxafrica amaehealth ambiententerprises amoriabond amplitude anaplan anchanto andurilindustries angi anthropic
apolloio appdirect appian appier appliedintuition applovin appnovation appsflyer apptronik aqr arcesiumllc archer56 arlosolutionsllc armada armissecurity
artefact artefactjobs asana asm assetliving asteralabs astranis attentive atwellgroup auctane aurorainnovation authenticbrandsgroup autoscout24 axicorpfinancialservicesptyltd axon
axs baidu bamboohr17 bandwidth banyansoftware basejobs beamtherapeutics benchmarkpt betsson betterhelp betterment beyondfinance beyondtrust bgeinc bigid
billcom billiontoone bird bitgo bitpanda blackbirdhealth blackcanyonconsulting blackduck blackforestlabs blankstreet blockchain bloomreach blueskyinnovators blueskytelepsych bonsbyhvacplumbing
bottomlinetechnologies bouldercare boxinc bpcs brainrocketltd brandtechplus braze breezeairways brex bridgebio btgpactual buildops butternutbox buyersedgeplatformrecruiting bythebayhealth
c6bank cabify caddellconstruction camp cannondesign canonical canto capco capitalontap capitalrx careaccess carecom careem careerteam cargurus
carmichaellynch caronsale carta casetify catonetworks caylent cdpjobs ceg celigo celonis centrumhealth cerebrassystems ceribell cfoinsights chainguard
chaosindustries chargepoint charlesriverassociates checkr chime chipcity chowbus cision clara classpass clear clearstreet clearwayjobs clickhouse clinchoice
cloudflare cloudsek cloverhealth clubmonaco clutch coalition cobblestoneenergy4 cockroachlabs coherehealth coinbase collibra commvault complyadvantage compunetinc comstock
conga connectwise constructionresources constructortech contentful convene convera cookunity coreweave cortica cranialtechnologies crederacampusrecruiting creditkarma crescolabs cresta
cribl crisprecruit criticalmass crunchyroll css cultureamp curaleaf cvx datacamp datadog dataiku dbtlabsinc decathlontechnology decimainternational dental365
dept devrev dexisconsultinggroup dialpad dianahealth94 digrestaurants diligentcorporation disco discord divergent dlrgroup doctolib doitintl doordashaustralia doordashcanada
doordashindia doordashinternational doordashmexico doordashusa doubleverify drayerpt drivewealth dropbox drweng dunnhumby duolingo dvtrading dxacirca earnin easygo
ebanx echodynecorp edgewoodpartnersinsurancecenter elastic eliotcommunityhumanservices elitedentalpartnersllc embed engine ennoblecare enova enviva eosfitness eositsolutions epicgames eqtcorporation
ernestpackagingsolutions esri ethoslife eucalyptus everlaw exadelinc faire fairlife familyofkidz faradayfuture fartherfinance fashionnova fastly feedzai fetch
feverup fictiv figma figureai financialtimes33 fireblocks firstmind five9 fivetran flagshippioneeringinc fletcherjonesautomotivegroup flex flexport flywheeldigital focusfinancialpartners
formlabs forter foundationriskpartners fourhands freeformfuturecorp freenow fschumacherco fundraiseup galaxydigitalservices gallup garnerhealth gatikaiinc generalmatter geniussports genscript
geotab getyourguide gitlab givedirectly gleanwork glencoreukwx globalaccelerator globalhealthcareexchangeinc glossgenius gocardless gofundme golin gomotive gongio goodbysilversteinpartners
gorjana gotion govini gr8tech grafanalabs grammarly graphcore greenthumbindustries groupon gsdm gtb guidelighthealth guidepoint guidepointsecurity guidepostmontessori
gusto harbingermotors harnessinc harpergroup harrowhealth havenhub hawthornemachineryco headlandsresearch headway heartflowinc hellofresh helsing hibu hightouch hillel
hoodhp hootsuite housecall hpiq hs hubspotjobs hudl hudsonrouge humanagency humaninterest humansignal hungaryomg hut8 hyperiondev hyphenconnect
ibkr icapitalnetwork iconcareers iherb imagentechnologies imc impact incode indiaomd industrialelectricmanufacturing infuse inhometherapy inmobi inspiremedicalsystemsinc instawork
insurtechinsights integrityrehabgroup interbrand intercom internaljobsatlush intrinsicrobotics invivyd ionos ionq isomorphiclabs iterable ivalua ixllearning janestreet jazzx-ai
jdsports jensenhughes jetbrains jfrog jumia jumptrading justworks k2spacecorporation kairospower kayak keepersecurity ketchumuscareers khanacademy kinders klaviyo
knowbe4 kodiak kolmacintegratedbehavioralhealth komodohealth krollbondratingagency kyocare kyowakirinusa90 landor lasenza later latitude launch2 launchdarkly leagueinc legendcareers
levio lgelectronics lgenergyaz liberate lifeskillsautismacademy lighthouse lightmatter lightricks lightspeedhq lightspeedhqfr lilasciences lpc lucidmotors lucidsoftware lush
lyft maintainx mangroup manychat map mark43 marqvision mavensecuritiesholdingltd mcadams mcmastercarr mcs mediabrands medier mejuri mentalhealthcenterofdenver
merceradvisors mercury metropolis midihealth mindbody minitab misfitsmarket mitsogoinc mixpanel modernanimal moia moloco mongodb moniepoint monroetractor
monzo morganmorganjobsapplynow motional mozilla mrbeastyoutube mthreerecruitingportal muonspace myriad360 n26 nabis natera nationallifeinsurancecompany nebius neo4j neoris
nerdy nerostechnologies netskope neuraflash neuralink neweratech newrelic nex nice nintendo nix northmarq novafounders nscaleoperationsukltd nubank
nuro octus oklo okta okx oliver olsson olympusproperty omgcamontreal omgcamontrealfr omgcaphd omguk omgus omgusannalect omgushs
omgusomd omgusphd omnicomhealth omnicommediagroupmxomg omnicomproduction oneacrefund onenergy onrunning opendoor opentable opj oportun optimalcare orchard oscar
oura overlandai pagerduty palmettocleantech pandadoc parloa payoneer paypay paypaycard peloton peregrinetechnologies perryellisinternationalretail perscholashires pfm pharmacann
philzcoffeecareers phoenixcontact phonepe physicsx pingidentity pinterestjobadvertisements pitchbookdata planetlabs platacard platformscience plscareers pmc pmg podium81 point72
pomelocare porternovelli postman powerdigitalmarketing precisionaq precisionmedicinegroup premiertruckrental presidents privateequityinsights prolific psiquantum pubmatic purestorage qualtrics quberesearchandtechnologies
queracomputinginc quince quintoandar raisin rapp razorpaysoftwareprivatelimited realchemistry rebuildmanufacturing recordedfuture recursionpharmaceuticals reddit redstoneresidential redwoodmaterials redwoodsoftware reformation
relativity reltio remotecom resultspt retailinsights rga riotgames ripple rithumliboard roblox rocketlab rockstargames roku roller roofstock
rubrik runwayml samsara samsungsemiconductor scaleai scandit scangroup schonfeld scopely scout24 scoutmotors secretariatadvisorsllc securityscorecard sezzle sharkninjaoperatingllc
shieldshealthsolutions shift4 shifttechnology shipbobinc shunnarahcareers siei sigmacomputing sigmoid silverado silvus similarweb simplifynext simtrabps skhynixamerica skildai-careers
skinclique skinlaundry skyscanner slice smartbear smartlyio smartsheet smavagmbh smcp smithrx sofi sohohouseco sollishealth sonobello sonyinteractiveentertainmentglobal
sonymusicentertainment sonypicturesimageworks sothebys spacex spauldingridge speechify spektrum spire spothopper springhealth66 spsnorthamerica squarepointcapital squarespace stackadapt stone
strategichr strivehealth stubhubinc studycontractors sumologic sunnyside superbet sustainabletalent swarmaero sweetgreen systemstechnologyresearch tailscale takealotcom taketwo talkdesk2
talkspacepsychiatry talkspacetherapist tanium tatari tbwachiatday teampicnic tecovas tegnainc tekion tenableinc teneolinkedin tenstorrent tenstorrentuniversity thealleninstitute theeconomistgroup
theknotworldwide themartinagency thenewyorktimes thenuclearcompany thequalitygroupgmbh1 thequalitygroupgmbh2 thetradedesk thinkacademyus threatlocker tide tines tipaltisolutions toast togetherai toogoodtogo
topcompare topsort torcrobotics torq toshibaglobalcommercesolutions tosscareers tpcengineeringholdingsllc trace3 tripactions tripadvisor triumvirateenvironmental trueanomalyinc tulip twilio twistbioscience
twitch twosixtechnologies uberfreight udemy unitedfirm upstart upwork usamechasp usconec usenourish vacasa vaco vailclinicincdbavailhealthhospital vardaspace varicent
vast vaxcyte veeamsoftware veracyte vercel veriff verifone veristainc verkada versaterm via viralnation virtu vmlcanadaen vmlenterprisesolutions
vonage voyagertechnologiesinc wargamingen waymo wayve webershandwick webflow wehrtyou welbehealth wikimedia wilsonelser winhomeinspection wizinc woolpert workato
workstream worldquant wpp wppmedia wrike xai xntltd xometry xometryeurope xund yipitdata yipitdatajobs yotpo zenoti zind-erprogram
zinnia zipcolimited ziprecruiter zocdoc zone5technologies zonecompanysoftwareconsultingllc zoominfo zscaler zuora zyngacareers
airbnb coinbase databricks datadog elastic fivetran flexport grammarly gusto hashicorp hubspotjobs instacart ironclad klarna lattice
loom lyft marqeta masterclass miro mongodb nerdwallet netlify newrelic noom okta opendoor palantir peloton persona
postman procore qualtrics ramp reddit rivian robinhood rubrik samsara scale seatgeek sift snap snyk squarespace
stripe tailscale temporal toast upstart zapier zendesk zillow zoomvideo block braze celonis cloudflare cockroachlabs
fastly figma gitlab plaid sofi twilio twitch uber webflow zscaler notion pinterest shopify spotify unity3d vanta
`.trim().split(/\s+/);

interface GHJobsResponse {
  jobs: Array<{ id: number; title: string }>;
  meta?: { total: number };
}

interface GHBoardResponse {
  name: string;
  content?: string;
}

const API = 'https://boards-api.greenhouse.io/v1/boards';

async function probeToken(token: string): Promise<CompanyDiscovery | null> {
  // Fetch jobs count
  const jobs = await fetchJson<GHJobsResponse>(`${API}/${token}/jobs?content=false`);
  if (!jobs?.jobs || jobs.jobs.length === 0) return null;

  const jobCount = jobs.meta?.total ?? jobs.jobs.length;

  // Fetch board name (actual company name)
  const board = await fetchJson<GHBoardResponse>(`${API}/${token}`);
  const companyName = board?.name || token;

  return {
    company_name: companyName,
    job_board_url: `https://boards.greenhouse.io/${token}`,
    estimated_jobs: jobCount,
    source: 'greenhouse',
    discovered_at: new Date().toISOString(),
  };
}

export async function discoverFromGreenhouse(maxCompanies = 800): Promise<CompanyDiscovery[]> {
  const uniqueTokens = [...new Set(TOKENS)];
  console.log(`Greenhouse: probing ${uniqueTokens.length} board tokens...`);

  const results = await processInBatches(uniqueTokens, 25, probeToken);

  // Sort by job count descending
  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);

  const total = results.reduce((sum, c) => sum + c.estimated_jobs, 0);
  console.log(`Greenhouse: ${results.length} active boards, ${total.toLocaleString()} total jobs`);

  return results.slice(0, maxCompanies);
}
