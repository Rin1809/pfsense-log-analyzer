import React from 'react';
import {
    Table,
    Thead,
    Tbody,
    Tr,
    Th,
    Td,
    Badge,
    Menu,
    MenuButton,
    MenuList,
    MenuItem,
    IconButton

} from '@chakra-ui/react';
import {
    ViewIcon,
    DownloadIcon,
    DeleteIcon,
    EmailIcon
} from '@chakra-ui/icons';
import { useLanguage } from '../../context/LanguageContext';

const ThreeDotsIcon = (props) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
        <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
    </svg>
);

const ReportsTable = ({ reports, onViewRaw, onViewTemplate, onDownload, onDelete }) => {
    const { t } = useLanguage();

    // Optimize: Pre-calculate derived data to avoid expensive checks in the render loop
    const processedReports = React.useMemo(() => {
        if (!reports || !Array.isArray(reports)) return [];

        return reports.map(report => {
            const stats = report.summary_stats || {};
            const details = report.analysis_details_markdown || ""; // Defensive: default to string

            const isWorkerFailed = details.includes("Worker Failed") || details.includes("Fatal Gemini Error");
            // Check if stats are empty/invalid. 
            // Note: explicit check for 'N/A' might be needed depending on backend
            const isStatsEmpty = Object.keys(stats).length === 0 || Object.values(stats).includes('N/A');
            const isError = isWorkerFailed || isStatsEmpty;

            return {
                ...report,
                formattedDate: new Date(report.generated_time).toLocaleString(), // Format once
                isError,
                badgeColor: isError ? 'red' : 'green',
                badgeTextKey: isError ? 'reportError' : 'reportSuccess'
            };
        });
    }, [reports]);

    return (
        <Table variant="simple">
            <Thead>
                <Tr>
                    <Th>{t('hostname')}</Th>
                    <Th>{t('type')}</Th>
                    <Th>{t('time')}</Th>
                    <Th>{t('status')}</Th>
                    <Th textAlign="right">{t('actions')}</Th>
                </Tr>
            </Thead>
            <Tbody>
                {processedReports.length > 0 ? (
                    processedReports.map((report) => (
                        <Tr key={report.path}>
                            <Td fontWeight="medium">{report.hostname}</Td>
                            <Td>
                                <Badge variant="outline" colorScheme="blue" fontSize="0.8em" fontWeight="normal">
                                    {report.type}
                                </Badge>
                            </Td>
                            <Td fontSize="sm" color="gray.500">
                                {report.formattedDate}
                            </Td>
                            <Td>
                                <Badge
                                    colorScheme={report.badgeColor}
                                    variant="subtle"
                                    fontWeight="normal"
                                    px={2} py={1}
                                    borderRadius="full"
                                >
                                    {t(report.badgeTextKey)}
                                </Badge>
                            </Td>
                            <Td textAlign="right">
                                <Menu>
                                    <MenuButton
                                        as={IconButton}
                                        icon={<ThreeDotsIcon style={{ width: '20px', height: '20px' }} />}
                                        variant="ghost"
                                        size="sm"
                                        aria-label="Options"
                                    />
                                    <MenuList>
                                        <MenuItem icon={<ViewIcon />} onClick={() => onViewRaw(report.path)}>
                                            {t('viewJson')}
                                        </MenuItem>
                                        <MenuItem icon={<EmailIcon />} onClick={() => onViewTemplate(report.path)}>
                                            {t('viewEmail')}
                                        </MenuItem>
                                        <MenuItem icon={<DownloadIcon />} onClick={() => onDownload(report.path)}>
                                            {t('download')}
                                        </MenuItem>
                                        <MenuItem icon={<DeleteIcon />} color="red.500" onClick={() => onDelete(report.path)}>
                                            {t('delete')}
                                        </MenuItem>
                                    </MenuList>
                                </Menu>
                            </Td>
                        </Tr>
                    ))
                ) : (
                    <Tr>
                        <Td colSpan={5} textAlign="center" py={6} color="gray.500">
                            {t('noFilesFound')}
                        </Td>
                    </Tr>
                )}
            </Tbody>
        </Table>
    );
};

export default ReportsTable;